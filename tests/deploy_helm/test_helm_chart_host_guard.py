"""The Helm chart must be reachable by the names it creates (RG-GUARD-04, RG-INSTALL-04).

The Host guard (core/security/host_guard.py) answers ``400 HOST_NOT_ALLOWED``
unless ``Host`` is localhost, an IP literal, the process's own hostname or a name
in ``CORTEXSIM_ALLOWED_HOSTS``. In a pod the hostname is the pod name, so it is
neither the Service name, nor an Ingress host, nor the Tailscale sidecar's name.
The chart's ConfigMap emitted a fixed key set with no ``CORTEXSIM_ALLOWED_HOSTS``
and no way to add one, so every access path the chart itself ships was refused
while the kubelet probes (pod IP, an IP literal) kept the pods Ready.

Two tiers:

* static checks that read the chart's files, which run everywhere;
* rendered checks that run ``helm template`` (a local render: nothing is applied
  and no cluster is contacted) and feed the rendered allowlist through the real
  ``HostGuardMiddleware``, so the chart is held to the guard's own syntax rather
  than to a string this test invented.

Without ``helm`` on PATH the rendered tier skips. ``CORTEXSIM_REQUIRE_HELM=1``
turns that skip into a failure, for a CI job that is supposed to have it: a
skipped guard reads exactly like a passing one.

Nothing here deploys the chart. A green run proves what the templates render,
not that a cluster serves it.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from security.host_guard import HostGuardMiddleware, parse_allowed_hosts

REPO = Path(__file__).resolve().parents[2]
CHART = REPO / "deploy" / "helm" / "eal-simulator"
MASTER = "0123456789abcdef0123456789abcdef01234567"  # 40 chars, test-only

HELM = shutil.which("helm")
REQUIRE_HELM = os.environ.get("CORTEXSIM_REQUIRE_HELM") == "1"


def _require_helm() -> str:
    if HELM is None:
        if REQUIRE_HELM:
            pytest.fail("CORTEXSIM_REQUIRE_HELM=1 but helm is not on PATH")
        pytest.skip("helm is not installed; the rendered chart checks did not run")
    return HELM


def _template(*sets: str, expect_ok: bool = True) -> subprocess.CompletedProcess:
    argv = [_require_helm(), "template", "rel", str(CHART),
            "--set", f"masterSecret.value={MASTER}"]
    for s in sets:
        argv += ["--set", s]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    if expect_ok:
        assert proc.returncode == 0, proc.stderr
    return proc


def _docs(*sets: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(_template(*sets).stdout) if isinstance(d, dict)]


def _one(docs: list[dict], kind: str, name_suffix: str = "") -> dict:
    hits = [d for d in docs if d.get("kind") == kind
            and d["metadata"]["name"].endswith(name_suffix)]
    assert len(hits) == 1, f"expected one {kind} *{name_suffix}, got {[d['metadata']['name'] for d in hits]}"
    return hits[0]


def _config(docs: list[dict]) -> dict:
    return _one(docs, "ConfigMap", "-config")["data"]


def _admitted(allowlist: str, host: str) -> bool:
    """Ask the real guard, as a pod would run it: its own hostname is the pod
    name and its FQDN lookup returns the same, so neither admits anything."""
    guard = HostGuardMiddleware(
        app=None,  # never called: is_allowed() does not dispatch
        allowed_hosts=parse_allowed_hosts(allowlist),
        hostname_fn=lambda: "rel-eal-simulator-api-7c9d8f6b5-x2x9q",
        fqdn_fn=lambda: "rel-eal-simulator-api-7c9d8f6b5-x2x9q",
    )
    return asyncio.run(guard.is_allowed(host))


# --------------------------------------------------------------------------
# Static: runs with no helm
# --------------------------------------------------------------------------

def test_helm_values_declare_the_host_guard_and_proxy_knobs():
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    api = values["api"]
    assert isinstance(api.get("allowedHosts"), list), "values.yaml has no api.allowedHosts list"
    assert isinstance(api.get("trustedProxies"), list), "values.yaml has no api.trustedProxies list"
    assert isinstance(api.get("extraEnv"), dict), "values.yaml has no api.extraEnv map"
    assert values.get("clusterDomain") == "cluster.local"


def test_helm_configmap_template_emits_the_host_allowlist():
    text = (CHART / "templates" / "configmap.yaml").read_text(encoding="utf-8")
    assert "CORTEXSIM_ALLOWED_HOSTS" in text, (
        "the chart never sets CORTEXSIM_ALLOWED_HOSTS, so SimCore refuses the "
        "Service name, every Ingress host and the Tailscale name with 400"
    )
    assert "CORTEXSIM_TRUSTED_PROXIES" in text


def test_helm_guard_helper_refuses_a_service_name_that_is_not_listed():
    """The control: the helper above can say no. Without this, every 'admitted'
    assertion below could pass on a guard that admits everything."""
    assert not _admitted("", "rel-eal-simulator-api.eal-simulator.svc.cluster.local:8888")
    assert _admitted("", "10.42.0.17:8888"), "an IP literal (what the kubelet probes use) is refused"


# --------------------------------------------------------------------------
# Rendered: needs helm
# --------------------------------------------------------------------------

def test_helm_default_render_admits_every_dns_name_of_its_own_service():
    docs = _docs()
    svc = _one(docs, "Service", "-api")
    name, ns = svc["metadata"]["name"], svc["metadata"]["namespace"]
    allow = _config(docs).get("CORTEXSIM_ALLOWED_HOSTS", "")
    for host in (name, f"{name}.{ns}", f"{name}.{ns}.svc", f"{name}.{ns}.svc.cluster.local"):
        assert _admitted(allow, f"{host}:8888"), (
            f"the chart's own Service name {host!r} is refused by the Host guard "
            f"(rendered CORTEXSIM_ALLOWED_HOSTS={allow!r})"
        )


def test_helm_api_and_worker_pods_read_the_configmap_that_carries_it():
    docs = _docs()
    cm = _one(docs, "ConfigMap", "-config")["metadata"]["name"]
    for suffix in ("-api", "-worker"):
        dep = _one(docs, "Deployment", suffix)
        container = dep["spec"]["template"]["spec"]["containers"][0]
        refs = [e["configMapRef"]["name"] for e in container.get("envFrom", []) if "configMapRef" in e]
        assert refs == [cm], f"{dep['metadata']['name']} does not read {cm}: {refs}"


def test_helm_a_changed_allowlist_rolls_the_pods():
    """Pods read the ConfigMap through envFrom, which is resolved once at pod
    start. Without a checksum on the pod template, `helm upgrade --set
    api.allowedHosts=...` updates the ConfigMap, restarts nothing, and SimCore
    keeps refusing the new name."""
    def _stamp(docs, suffix):
        meta = _one(docs, "Deployment", suffix)["spec"]["template"]["metadata"]
        return (meta.get("annotations") or {}).get("checksum/config")

    before, after = _docs(), _docs("api.allowedHosts[0]=pov.customer.example")
    for suffix in ("-api", "-worker"):
        assert _stamp(before, suffix), f"the {suffix} pod template carries no checksum/config"
        assert _stamp(before, suffix) != _stamp(after, suffix), (
            f"changing api.allowedHosts does not change the {suffix} pod template"
        )
    assert _stamp(before, "-api") == _stamp(_docs(), "-api"), "the checksum is not deterministic"


def test_helm_namespace_and_cluster_domain_follow_the_values():
    docs = _docs("namespace=pov", "clusterDomain=corp.internal")
    allow = _config(docs)["CORTEXSIM_ALLOWED_HOSTS"]
    assert _admitted(allow, "rel-eal-simulator-api.pov.svc.corp.internal:8888"), allow
    assert not _admitted(allow, "rel-eal-simulator-api.eal-simulator.svc.cluster.local:8888"), allow


def test_helm_ingress_hosts_are_admitted_when_the_ingress_is_enabled():
    docs = _docs(
        "ingress.enabled=true",
        "ingress.hosts[0].host=cortexsim.customer-lab.example",
        "ingress.hosts[0].paths[0].path=/",
        "ingress.hosts[1].host=*.pov.example",
        "ingress.hosts[1].paths[0].path=/",
    )
    rules = [r["host"] for r in _one(docs, "Ingress")["spec"]["rules"]]
    assert rules == ["cortexsim.customer-lab.example", "*.pov.example"]
    allow = _config(docs)["CORTEXSIM_ALLOWED_HOSTS"]
    assert _admitted(allow, "cortexsim.customer-lab.example"), allow
    assert _admitted(allow, "sim.pov.example"), f"a wildcard Ingress host is refused: {allow}"


def test_helm_ingress_hosts_are_not_admitted_while_the_ingress_is_disabled():
    docs = _docs("ingress.hosts[0].host=cortexsim.customer-lab.example",
                 "ingress.hosts[0].paths[0].path=/")
    assert not _admitted(_config(docs)["CORTEXSIM_ALLOWED_HOSTS"], "cortexsim.customer-lab.example")


def test_helm_tailscale_hostname_is_admitted_when_the_sidecar_is_enabled():
    off = _config(_docs())["CORTEXSIM_ALLOWED_HOSTS"]
    assert not _admitted(off, "cortexsim-eal:8888")
    on = _config(_docs("tailscale.enabled=true"))["CORTEXSIM_ALLOWED_HOSTS"]
    assert _admitted(on, "cortexsim-eal:8888"), on


def test_helm_operator_names_are_added_not_substituted():
    docs = _docs("api.allowedHosts[0]=cortexsim-eal.tail1234.ts.net",
                 "api.allowedHosts[1]=POV.Customer.Example")
    allow = _config(docs)["CORTEXSIM_ALLOWED_HOSTS"]
    assert _admitted(allow, "cortexsim-eal.tail1234.ts.net:8888"), allow
    assert _admitted(allow, "pov.customer.example"), allow
    assert _admitted(allow, "rel-eal-simulator-api:8888"), "operator names replaced the Service names"
    names = allow.split(",")
    assert len(names) == len(set(names)), f"duplicate names rendered: {names}"


def test_helm_trusted_proxies_render_only_when_set():
    assert "CORTEXSIM_TRUSTED_PROXIES" not in _config(_docs()), (
        "an empty value must not be rendered: the default is to ignore X-Forwarded-For"
    )
    data = _config(_docs("api.trustedProxies[0]=10.42.0.0/16", "api.trustedProxies[1]=10.43.0.5/32"))
    assert data["CORTEXSIM_TRUSTED_PROXIES"] == "10.42.0.0/16,10.43.0.5/32"


def test_helm_extra_env_passes_through_to_the_configmap():
    data = _config(_docs("api.extraEnv.CORTEXSIM_CORS_ORIGINS=https://console.pov.example"))
    assert data["CORTEXSIM_CORS_ORIGINS"] == "https://console.pov.example"


@pytest.mark.parametrize("key, pointer", [
    ("CORTEXSIM_ALLOWED_HOSTS", "api.allowedHosts"),
    ("CORTEXSIM_TRUSTED_PROXIES", "api.trustedProxies"),
    ("CORTEXSIM_SECRET", "masterSecret"),
])
def test_helm_extra_env_cannot_shadow_a_key_the_chart_manages(key, pointer):
    """Two definitions of one key in a ConfigMap render as a duplicate mapping
    key, and which one wins is the YAML parser's business. And the master key
    must never land in a ConfigMap."""
    proc = _template(f"api.extraEnv.{key}=x", expect_ok=False)
    assert proc.returncode != 0, f"extraEnv.{key} rendered instead of being refused"
    assert key in proc.stderr and pointer in proc.stderr, proc.stderr
