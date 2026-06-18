"""Tier-C increment 2 — observed-signal assertion engine (stdlib only).

Increment 1 (deploy/tier-c/) proves a CortexSim push bundle can be executed in
an isolated, audited, network-sinkholed container and produce a structured
``observed-signals.json`` ground-truth summary. This module is increment 2: it
turns that ground truth into a *verdict* by checking the observed signals
against a per-scenario **expectation** — the formal version of the mapping
table in the Tier-C README ("How the audit output maps to expected_detections").

Design:

  * A :class:`TierCExpectation` declares what a scenario MUST produce in the
    isolated sandbox: the identities it forks under, the credential files it
    reads, whether it egresses to the network, and the binaries that must fire.
  * :data:`REFERENCE_EXPECTATIONS` hand-authors those for the three reference
    scenarios called out in the README (SIM-EDR-001, SIM-CDR-001, SIM-MP-004).
  * :func:`derive_expectation` synthesises a baseline expectation from any
    scenario dict (identities from each step's ``identity``; credential / network
    heuristics from the commands) so the engine also works for scenarios without
    a hand-authored spec.
  * :func:`evaluate` compares an expectation against an ``observed-signals.json``
    dict and returns an :class:`AssertionReport` with a per-check verdict.

Audit-mode awareness: in ``degraded`` mode (no kernel audit netlink — common on
nested/rootless Docker and some CI) the ``signals.*.count`` fields are all 0, so
count-based checks (credential reads, network, executables) cannot be asserted —
they are reported as ``skipped`` rather than ``failed``. Identity checks still
run because the harness logs ``identity=<user>`` to stdout independently of
auditd. This mirrors the README's degraded-mode contract.

Stdlib only — no imports beyond the standard library — so it runs inside the
bare runner image and is importable by both the operator script and the test
suite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# Identities that run WITHOUT an impersonation wrapper — they do not produce a
# setuid transition, so they are not asserted as identity signals. Mirrors
# spec/identity_harness.json direct_identities.
DIRECT_IDENTITIES = frozenset({"root", "direct", "container-runtime"})

# Canonical credential artifacts the audit ruleset watches (deploy/tier-c/
# audit.rules). A scenario command touching any of these should raise a
# cortexsim_creds record.
CREDENTIAL_PATHS = (
    "/etc/shadow",
    "/etc/passwd",
    ".aws/credentials",
    ".ssh",
    "/root/.aws",
    "serviceaccount/token",
)


@dataclass(frozen=True)
class TierCExpectation:
    """What a scenario must produce when executed in the Tier-C sandbox.

    Every field is optional so a partial expectation is valid; ``evaluate``
    only checks the dimensions that are populated.
    """

    scenario_id: str
    #: Service-account identities the run must fork under (non-direct only).
    identities: frozenset[str] = field(default_factory=frozenset)
    #: True if the run is expected to read a credential file (cortexsim_creds).
    expect_credential_access: bool = False
    #: True if the run is expected to egress to the network (cortexsim_net /
    #: sinkhole http.jsonl).
    expect_network: bool = False
    #: Binaries that must appear in signals.exec.executables (basename match).
    expect_executables: frozenset[str] = field(default_factory=frozenset)
    #: Human note describing the scenario's Tier-C intent.
    note: str = ""


@dataclass
class CheckResult:
    """One assertion outcome."""

    name: str
    status: str  # "pass" | "fail" | "skip"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class AssertionReport:
    """Aggregate verdict for one scenario run."""

    scenario_id: str
    audit_mode: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def passed(self) -> bool:
        """True when no check failed (skips are tolerated)."""
        return not self.failed

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(CheckResult(name, status, detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "audit_mode": self.audit_mode,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Reference expectations (README: SIM-EDR-001, SIM-CDR-001, SIM-MP-004)
# ---------------------------------------------------------------------------

REFERENCE_EXPECTATIONS: dict[str, TierCExpectation] = {
    "SIM-EDR-001": TierCExpectation(
        scenario_id="SIM-EDR-001",
        identities=frozenset({"www-data"}),
        expect_credential_access=True,   # reads /etc/shadow, ~/.ssh, /proc/*/environ
        expect_network=True,             # step-05 curls mimipenguin.sh
        expect_executables=frozenset({"cat", "grep"}),
        note="Credential dumping as www-data — shadow/ssh/proc reads + tool fetch.",
    ),
    "SIM-CDR-001": TierCExpectation(
        scenario_id="SIM-CDR-001",
        # container-runtime is a DIRECT identity (no setuid wrapper) — there is
        # no identity signal to assert; the value of Tier-C here is the
        # credential (serviceaccount token) read + the curl-pipe-bash egress.
        identities=frozenset(),
        expect_credential_access=True,   # /var/run/secrets/.../serviceaccount/token
        expect_network=True,             # curls deepce.sh / linpeas.sh
        expect_executables=frozenset({"curl"}),
        note="Container enumeration — serviceaccount token read + tool egress.",
    ),
    "SIM-MP-004": TierCExpectation(
        scenario_id="SIM-MP-004",
        identities=frozenset({"www-data"}),
        expect_credential_access=True,   # harvests cloud credential material
        expect_network=True,             # APT29 cloud cred-theft egress
        expect_executables=frozenset(),
        note="APT29 cloud credential theft as www-data — cred read + cloud egress.",
    ),
}


# ---------------------------------------------------------------------------
# Derivation from an arbitrary scenario dict
# ---------------------------------------------------------------------------

_CRED_CMD_RE = re.compile(
    r"/etc/shadow|/etc/passwd|\.aws/credentials|\.ssh|serviceaccount/token|"
    r"/proc/\d+/environ|unshadow|mimipenguin",
    re.IGNORECASE,
)
_NET_CMD_RE = re.compile(
    r"\bcurl\b|\bwget\b|\bnc\b|\bncat\b|/dev/tcp/|\bcurl -|https?://|"
    r"\bscp\b|\bssh\b",
    re.IGNORECASE,
)


def derive_expectation(scenario: dict[str, Any]) -> TierCExpectation:
    """Synthesise a baseline Tier-C expectation from a scenario dict.

    Identities: every step ``identity`` that is a service account (not a direct
    identity). Credential / network expectations: True if ANY step command
    matches the credential / network heuristics. Executables: left empty (the
    hand-authored reference specs are authoritative for binary-level checks).
    """
    sid = scenario.get("scenario_id", "UNKNOWN")
    steps = scenario.get("steps") or []
    identities: set[str] = set()
    expect_cred = False
    expect_net = False
    for step in steps:
        ident = (step.get("identity") or "").strip()
        if ident and ident not in DIRECT_IDENTITIES:
            identities.add(ident)
        cmd = step.get("command") or ""
        if _CRED_CMD_RE.search(cmd):
            expect_cred = True
        if _NET_CMD_RE.search(cmd):
            expect_net = True
    return TierCExpectation(
        scenario_id=sid,
        identities=frozenset(identities),
        expect_credential_access=expect_cred,
        expect_network=expect_net,
        note="auto-derived from scenario steps",
    )


def expectation_for(scenario: dict[str, Any]) -> TierCExpectation:
    """Return the authoritative hand-authored expectation for a scenario if one
    exists, else derive a baseline from the scenario dict."""
    sid = scenario.get("scenario_id", "")
    return REFERENCE_EXPECTATIONS.get(sid) or derive_expectation(scenario)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _observed_identities(observed: dict[str, Any]) -> set[str]:
    """Union of identities visible from setuid signals and harness stdout."""
    out: set[str] = set()
    signals = observed.get("signals") or {}
    setid = signals.get("setid") or {}
    for u in setid.get("uids") or []:
        out.add(str(u))
    for u in observed.get("harness_identities_from_stdout") or []:
        out.add(str(u))
    return out


def _count(observed: dict[str, Any], key: str) -> int:
    signals = observed.get("signals") or {}
    entry = signals.get(key) or {}
    try:
        return int(entry.get("count", 0))
    except (TypeError, ValueError):
        return 0


def _executables(observed: dict[str, Any]) -> set[str]:
    signals = observed.get("signals") or {}
    execs = signals.get("exec") or {}
    out: set[str] = set()
    for path in execs.get("executables") or []:
        out.add(str(path).rsplit("/", 1)[-1])  # basename
    return out


def _sinkhole_hits(observed: dict[str, Any]) -> int:
    """Number of HTTP(S) requests the sinkhole logged, if surfaced."""
    sink = observed.get("sinkhole") or {}
    try:
        return int(sink.get("http_request_count", 0))
    except (TypeError, ValueError):
        return 0


def evaluate(
    expectation: TierCExpectation, observed: dict[str, Any]
) -> AssertionReport:
    """Check ``observed-signals.json`` against an expectation.

    Count-based checks (credential, network, executables) are only asserted in
    ``auditd`` mode; in ``degraded`` mode they are reported ``skip`` because the
    counts are structurally 0. Identity checks always run.
    """
    audit_mode = str(observed.get("audit_mode", "unknown"))
    report = AssertionReport(scenario_id=expectation.scenario_id, audit_mode=audit_mode)

    # 1. Bundle executed cleanly.
    exit_code = observed.get("bundle_exit_code")
    if exit_code is None:
        report.add("bundle_executed", "skip", "no bundle_exit_code in summary")
    elif exit_code == 0:
        report.add("bundle_executed", "pass", "bundle exited 0")
    else:
        report.add("bundle_executed", "fail", f"bundle exited {exit_code}")

    # 2. Identities — always assertable (stdout-derived).
    if expectation.identities:
        observed_ids = _observed_identities(observed)
        missing = sorted(expectation.identities - observed_ids)
        if missing:
            report.add(
                "identities", "fail",
                f"expected identities not observed: {missing} "
                f"(observed: {sorted(observed_ids)})",
            )
        else:
            report.add(
                "identities", "pass",
                f"all expected identities forked: {sorted(expectation.identities)}",
            )

    degraded = audit_mode != "auditd"

    # 3. Credential access (auditd-only).
    if expectation.expect_credential_access:
        if degraded:
            report.add("credential_access", "skip",
                       f"audit_mode={audit_mode}: cred count not available")
        elif _count(observed, "creds") > 0:
            report.add("credential_access", "pass",
                       f"{_count(observed, 'creds')} credential read(s) observed")
        else:
            report.add("credential_access", "fail",
                       "expected a credential-file read but cortexsim_creds count is 0")

    # 4. Network egress (auditd net count OR sinkhole hits).
    if expectation.expect_network:
        net = _count(observed, "net")
        sink = _sinkhole_hits(observed)
        if net > 0 or sink > 0:
            report.add("network_egress", "pass",
                       f"net connects={net}, sinkhole hits={sink}")
        elif degraded and sink == 0:
            report.add("network_egress", "skip",
                       f"audit_mode={audit_mode} and no sinkhole hits surfaced")
        else:
            report.add("network_egress", "fail",
                       "expected network egress but no connect/sinkhole signal")

    # 5. Executables (auditd-only).
    if expectation.expect_executables:
        if degraded:
            report.add("executables", "skip",
                       f"audit_mode={audit_mode}: exec list not available")
        else:
            observed_exes = _executables(observed)
            missing = sorted(expectation.expect_executables - observed_exes)
            if missing:
                report.add("executables", "fail",
                           f"expected binaries did not fire: {missing}")
            else:
                report.add("executables", "pass",
                           f"all expected binaries fired: "
                           f"{sorted(expectation.expect_executables)}")

    return report


def evaluate_scenario(
    scenario: dict[str, Any], observed: dict[str, Any]
) -> AssertionReport:
    """Convenience: pick the right expectation for ``scenario`` and evaluate."""
    return evaluate(expectation_for(scenario), observed)
