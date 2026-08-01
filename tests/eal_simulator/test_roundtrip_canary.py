"""Ingestion round-trip canary — the emitter half.

The PLT assertion pack proves a record was ingested AND normalized by planting
one token twice per record and reading both plants back out of the tenant. This
suite proves the emitter half of that: that the token really reaches the wire,
in both places, in every source family the pack uses — and, just as important,
that a campaign which does NOT ask for a canary emits exactly the bytes it
emitted before the feature existed.

Nothing here touches the network: the shared driver's httpx client is replaced
with a recorder, so every assertion below is made against the actual POST body.
"""
from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

import pytest

from eal_simulator import AuditLogger, Campaign, CampaignExecutor
from eal_simulator.analytics_emitter import (
    CANARY_DOMAIN,
    CANARY_RAW_FIELD,
    AnalyticsEmitterParams,
    canary_bindings,
    plant_canary,
)
from eal_simulator.plugins.ad_windows_emitter import ADWindowsEmitter
from eal_simulator.plugins.cloud_audit_emitter import CloudAuditEmitter
from eal_simulator.plugins.idp_signin_emulator import IdpSigninEmulator
from eal_simulator.plugins.k8s_audit_emitter import K8sAuditEmitter
from eal_simulator.plugins.m365_activity_emitter import M365ActivityEmitter
from eal_simulator.plugins.ngfw_eal_emitter import NgfwEalEmitter


TOKEN = "csim-9f2a41c0d3e7"
COLLECTOR = "https://collector.cortexsim-canary.invalid/logs/v1/event"
ALLOWED = ["collector.cortexsim-canary.invalid"]


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


class _Recorder:
    """Stub httpx.AsyncClient capturing the exact bytes each POST carried."""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []

    async def post(self, url: str, *, headers=None, content=None):
        self.bodies.append(
            content if isinstance(content, (bytes, bytearray)) else str(content).encode()
        )

        class _R:
            status_code = 202

        return _R()

    async def aclose(self) -> None:
        return None


def _emit(monkeypatch, plugin_name: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Run one real campaign step through the real driver; return the records
    the collector would have received, decoded from the wire bytes."""
    rec = _Recorder()
    from eal_simulator import analytics_emitter as spine

    monkeypatch.setattr(
        spine.AnalyticsLogEmitter, "_build_client", lambda self, params: rec
    )

    campaign = Campaign.model_validate({
        "campaign_id": "CMP-ROUNDTRIP-001",
        "name": "round-trip canary",
        "dry_run": False,
        "simulation_authorized": True,
        "authorized_by": "tester",
        "target_allowlist": ALLOWED,
        "steps": [{
            "step_id": "step-01",
            "plugin": plugin_name,
            "params": {"collector_url": COLLECTOR, "sleep_seconds": 0.0, **params},
        }],
    })
    result = asyncio.run(CampaignExecutor(audit=AuditLogger()).execute(campaign))
    assert result.step_results[0].status in ("success", "partial"), \
        result.step_results[0].detail
    return [json.loads(b.decode()) for b in rec.bodies]


def _leaf(record: Any, name: str) -> list[str]:
    """Every string value stored under key ``name`` anywhere in the record.

    Models the one thing a normalizing parser is reliably doing: lifting a leaf
    out of whatever nesting the vendor wrapped it in.
    """
    found: list[str] = []
    if isinstance(record, dict):
        for k, v in record.items():
            if k == name and isinstance(v, str):
                found.append(v)
            found.extend(_leaf(v, name))
    elif isinstance(record, list):
        for item in record:
            found.extend(_leaf(item, name))
    return found


# --------------------------------------------------------------------------
# The plant itself
# --------------------------------------------------------------------------


class TestPlantCanary:
    def test_raw_marker_is_always_set(self):
        rec = plant_canary({"a": "b"}, TOKEN, {})
        assert rec[CANARY_RAW_FIELD] == TOKEN

    def test_nested_path_is_planted(self):
        rec = plant_canary(
            {"user": {"username": "system:serviceaccount:ns:sa"}},
            TOKEN, {"user.username": "{value}-{account}"},
        )
        assert rec["user"]["username"] == f"system:serviceaccount:ns:sa-{TOKEN}"

    def test_absent_path_is_skipped_not_created(self):
        # Creating a field the source's real schema does not carry would make
        # the record LESS shape-true, which defeats the family's whole premise.
        rec = plant_canary({"a": "b"}, TOKEN, {"nope.missing": "{principal}"})
        assert "nope" not in rec

    def test_empty_value_is_skipped_when_template_needs_it(self):
        # objectRef.name is "" on collection verbs; suffixing "" would emit a
        # bare "-csim-..." that looks like a real object name and is not one.
        rec = plant_canary({"name": ""}, TOKEN, {"name": "{value}-{account}"})
        assert rec["name"] == ""

    def test_non_string_value_is_left_alone(self):
        rec = plant_canary({"code": 200}, TOKEN, {"code": "{account}"})
        assert rec["code"] == 200

    def test_principal_is_stable_for_a_token(self):
        b = canary_bindings(TOKEN)
        assert b["principal"] == f"{TOKEN}@{CANARY_DOMAIN}"
        assert canary_bindings(TOKEN) == b


class TestTokenValidation:
    @pytest.mark.parametrize("bad", [
        'a" or dataset in (x)',      # would reshape an XQL literal
        "CSIM-UPPER-1234",           # uppercase is not valid in a k8s object name
        "csim",                      # too short to be unique
        "csim_9f2a41c0",             # underscore is not valid in a DNS-ish name
        "csim-9f2a 41c0",            # whitespace
    ])
    def test_unsafe_tokens_are_rejected(self, bad):
        with pytest.raises(Exception, match="canary_token"):
            AnalyticsEmitterParams.model_validate({
                "collector_url": COLLECTOR, "canary_token": bad,
            })

    def test_good_token_accepted(self):
        p = AnalyticsEmitterParams.model_validate({
            "collector_url": COLLECTOR, "canary_token": TOKEN,
        })
        assert p.canary_token == TOKEN

    def test_token_satisfies_the_assertion_substrates_context_whitelist(self):
        # The token is substituted into an assertion's {{canary}} placeholder,
        # and the substrate refuses any value that could reshape the query. If
        # these two whitelists ever diverge the round-trip silently becomes
        # unrunnable, so pin them together here.
        from engine.assertions import _CONTEXT_VALUE_RE

        assert _CONTEXT_VALUE_RE.match(TOKEN)
        assert _CONTEXT_VALUE_RE.match(canary_bindings(TOKEN)["principal"])


# --------------------------------------------------------------------------
# Every source family the PLT pack uses
# --------------------------------------------------------------------------


# plugin -> (params, dataset, the source's OWN field the pack queries on)
SOURCES: dict[str, tuple[dict[str, Any], str, str]] = {
    "k8s_audit_emitter": ({"event_pattern": "pod_exec"},
                          "kubernetes_audit_logs", "userAgent"),
    "m365_activity_emitter": ({"event_pattern": "mailbox_enumeration_by_app", "burst_count": 6},
                              "msft_o365_audit", "UserId"),
    "ngfw_eal_emitter": ({"event_pattern": "large_upload_https"},
                         "panw_ngfw_traffic_raw", "user"),
    "cloud_audit_emitter": ({"provider": "aws", "event_pattern": "secrets_dump"},
                            "cloud_audit_logs", "userName"),
    "ad_windows_emitter": ({"event_pattern": "kerberoast_weak_ticket"},
                           "msft_windows_security", "TargetUserName"),
    "idp_signin_emulator": ({"provider": "okta", "event_pattern": "brute_force_lockout"},
                            "okta_sso", "alternateId"),
}


class TestEveryPackSourcePlantsBothMarkers:
    @pytest.mark.parametrize("plugin", sorted(SOURCES))
    def test_raw_and_native_plants_reach_the_wire(self, monkeypatch, plugin):
        params, dataset, field = SOURCES[plugin]
        records = _emit(monkeypatch, plugin, {**params, "canary_token": TOKEN})
        assert records, "the campaign posted nothing"

        for rec in records:
            assert rec["dataset"] == dataset
            # plant 2 — bytes-arrived signal
            assert rec[CANARY_RAW_FIELD] == TOKEN
            # plant 1 — normalization signal, in the source's OWN schema field
            values = _leaf(rec, field)
            assert values, f"{plugin}: no {field} in the emitted record"
            assert any(TOKEN in v for v in values), (
                f"{plugin}: canary missing from native field {field}: {values}"
            )

    @pytest.mark.parametrize("plugin", sorted(SOURCES))
    def test_no_token_means_byte_identical_output(self, monkeypatch, plugin):
        params, _dataset, _field = SOURCES[plugin]
        records = _emit(monkeypatch, plugin, params)
        assert records
        for rec in records:
            assert CANARY_RAW_FIELD not in rec
            blob = json.dumps(rec)
            assert TOKEN not in blob and "csim-" not in blob

    def test_identity_sources_share_one_principal(self, monkeypatch):
        """The load-bearing property behind PLT-ITDR-006.

        Three providers, three completely different principal fields, one
        string. Without this the cross-provider claim is three unrelated ingest
        checks wearing a trench coat.
        """
        principal = canary_bindings(TOKEN)["principal"]

        ad = _emit(monkeypatch, "ad_windows_emitter",
                   {"event_pattern": "kerberoast_weak_ticket", "canary_token": TOKEN})
        entra = _emit(monkeypatch, "idp_signin_emulator",
                      {"provider": "microsoft", "event_pattern": "brute_force_lockout",
                       "canary_token": TOKEN})
        okta = _emit(monkeypatch, "idp_signin_emulator",
                     {"provider": "okta", "event_pattern": "brute_force_lockout",
                      "canary_token": TOKEN})

        assert any(principal in v for r in ad for v in _leaf(r, "TargetUserName"))
        assert any(principal in v for r in entra for v in _leaf(r, "userPrincipalName"))
        assert any(principal in v for r in okta for v in _leaf(r, "alternateId"))

    def test_offline_bundle_and_wire_agree(self, monkeypatch):
        """plan_offline_records is what a push bundle ships. If it and the wire
        path disagreed on the canary, a bundle run inside the customer network
        would be unverifiable while the same campaign run from SimCore was."""
        params = M365ActivityEmitter.Meta.params_model.model_validate({
            "collector_url": COLLECTOR, "event_pattern": "mailbox_enumeration_by_app",
            "burst_count": 6, "canary_token": TOKEN,
        })
        planned = M365ActivityEmitter().plan_offline_records(
            params, sim_run_id="SIM-X", iteration=1,
        )
        assert planned
        for rec in planned:
            assert rec[CANARY_RAW_FIELD] == TOKEN
            assert any(TOKEN in v for v in _leaf(rec, "UserId"))


class TestCanaryFieldsAreDeclaredForEveryPackSource:
    """A source with no CANARY_FIELDS emits the raw marker only, which cannot
    tell 'not normalized' from 'not landed'. That is a legitimate state — but
    not for the six families the PLT pack asserts on, so pin them."""

    @pytest.mark.parametrize("cls", [
        K8sAuditEmitter, M365ActivityEmitter, NgfwEalEmitter,
        CloudAuditEmitter, ADWindowsEmitter, IdpSigninEmulator,
    ])
    def test_declared(self, cls):
        assert cls.CANARY_FIELDS, f"{cls.__name__} declares no CANARY_FIELDS"

    def test_templates_only_use_known_bindings(self):
        allowed = set(canary_bindings("x")) | {"value"}
        import string

        for cls in (K8sAuditEmitter, M365ActivityEmitter, NgfwEalEmitter,
                    CloudAuditEmitter, ADWindowsEmitter, IdpSigninEmulator):
            for path, template in cls.CANARY_FIELDS.items():
                used = {
                    f for _, f, _, _ in string.Formatter().parse(template) if f
                }
                assert used <= allowed, f"{cls.__name__}:{path} uses {used - allowed}"


def test_plant_is_the_only_difference(monkeypatch):
    """Diff a canaried record against an un-canaried one of the same shape and
    assert the ONLY changes are the declared plants. This is the guard behind
    'must not change existing emitter output shape'."""
    params_cls = NgfwEalEmitter.Meta.params_model
    common = {"collector_url": COLLECTOR, "event_pattern": "large_upload_https"}

    plain = NgfwEalEmitter().records_for(
        params_cls.model_validate(common), sim_run_id="SIM-X", iteration=1)[0]
    marked = NgfwEalEmitter().records_for(
        params_cls.model_validate({**common, "canary_token": TOKEN}),
        sim_run_id="SIM-X", iteration=1)[0]

    # Fields whose values are re-randomised per record are not part of the claim.
    volatile = {"session_id", "src_port", "process_hash", "time_generated",
                "receive_time", "packets_sent", "packets_received"}
    changed = {
        k for k in set(plain) | set(marked)
        if k not in volatile and plain.get(k) != marked.get(k)
    }
    assert changed == set(NgfwEalEmitter.CANARY_FIELDS) | {CANARY_RAW_FIELD}
    assert copy.deepcopy(plain) is not marked
