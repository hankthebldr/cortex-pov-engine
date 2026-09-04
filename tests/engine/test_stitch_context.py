"""
Unit tests for ``core/engine/stitch_context.py`` — the Stitch Context resolver.

The load-bearing assertions:

* **Determinism.** Same seed → byte-identical binding; distinct seeds → distinct.
* **Round-trip.** :meth:`StitchBinding.as_xdr_columns` fed back into the REAL
  ``causality_graph._entities()`` recovers all eight shared keys byte-identically
  — the resolver is genuinely the inverse of the coalescer, not a lookalike.
* **Fail-closed.** Unknown directive / incompatible key / unknown key /
  both-or-neither ``{literal|resolve}`` all raise ``StitchContextValidationError``.
* **Delegation.** ``canary_principal`` reuses ``analytics_emitter.canary_bindings``.
* **from_agent** reads the launch target (dict + ORM-shaped), sentinel on absence.
"""

from __future__ import annotations

import pytest

from engine.causality_graph import _entities
from engine.stitch_context import (
    DIRECTIVE_COMPAT,
    DIRECTIVES,
    ENTITY_KEYS,
    StitchBinding,
    StitchContextValidationError,
    resolve_stitch_context,
)
from eal_simulator.analytics_emitter import canary_bindings


# A spec exercising every leg: literals, both auto_* directives, the canary and
# the container id. from_agent is exercised separately (it needs a target).
FULL_SPEC = {
    "host": {"literal": "jumpbox-01"},
    "src_ip": {"resolve": "auto_ip"},
    "dst_ip": {"literal": "203.0.113.10"},
    "src_port": {"resolve": "auto_port"},
    "dst_port": {"literal": 443},
    "protocol": {"literal": "tcp"},
    "container_id": {"resolve": "auto_container_id"},
    "account": {"resolve": "canary_principal"},
    "cloud_resource": {"literal": "arn:aws:s3:::acme-logs"},
}


# ---------------------------------------------------------------------------
# Frozen vocabulary
# ---------------------------------------------------------------------------


def test_entity_keys_are_the_nine_in_order():
    assert ENTITY_KEYS == (
        "host",
        "src_ip",
        "dst_ip",
        "src_port",
        "dst_port",
        "protocol",
        "container_id",
        "account",
        "cloud_resource",
    )


def test_directive_set_is_the_closed_six():
    assert DIRECTIVES == {
        "auto_ip",
        "auto_port",
        "auto_5tuple",
        "canary_principal",
        "from_agent",
        "auto_container_id",
    }


# ---------------------------------------------------------------------------
# None / empty ⇒ no-op
# ---------------------------------------------------------------------------


def test_none_spec_returns_none():
    assert resolve_stitch_context(None, seed="run-1") is None


def test_empty_spec_returns_none():
    assert resolve_stitch_context({}, seed="run-1") is None


def test_all_none_entries_collapse_to_none():
    # A Pydantic model with every field unset dumps to {k: None} — a no-op.
    assert resolve_stitch_context({k: None for k in ENTITY_KEYS}, seed="run-1") is None


def test_model_dump_spec_is_accepted():
    class _FakeSchema:
        def model_dump(self):
            return {"dst_port": {"literal": 8080}}

    b = resolve_stitch_context(_FakeSchema(), seed="run-1")
    assert b is not None
    assert b.get("dst_port") == 8080


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_seed_identical_binding():
    a = resolve_stitch_context(FULL_SPEC, seed="run-XYZ")
    b = resolve_stitch_context(FULL_SPEC, seed="run-XYZ")
    assert a.values == b.values
    assert a.as_raw() == b.as_raw()
    assert a.as_xdr_columns() == b.as_xdr_columns()
    assert a.as_xdm() == b.as_xdm()
    assert a.principal == b.principal


def test_distinct_seeds_distinct_binding():
    a = resolve_stitch_context(FULL_SPEC, seed="run-A")
    b = resolve_stitch_context(FULL_SPEC, seed="run-B")
    # The seeded legs must differ; the literals stay equal.
    assert a.src_ip != b.src_ip
    assert a.src_port != b.src_port
    assert a.container_id != b.container_id
    assert a.account != b.account
    assert a.principal != b.principal
    assert a.dst_ip == b.dst_ip == "203.0.113.10"  # literal unchanged


def test_literal_passthrough_verbatim():
    b = resolve_stitch_context(
        {"dst_port": {"literal": 443}, "cloud_resource": {"literal": "arn:x"}},
        seed="run-1",
    )
    assert b.get("dst_port") == 443  # int preserved, not stringified
    assert b.get("cloud_resource") == "arn:x"


# ---------------------------------------------------------------------------
# THE round-trip: as_xdr_columns() → real _entities() recovers all eight
# ---------------------------------------------------------------------------


def test_as_xdr_columns_round_trips_through_real_entities():
    b = resolve_stitch_context(FULL_SPEC, seed="run-RT")
    recovered = _entities(result=b.as_xdr_columns(), observation=None)
    for key in (
        "host",
        "src_ip",
        "dst_ip",
        "src_port",
        "dst_port",
        "protocol",
        "container_id",
        "account",
    ):
        assert recovered[key] == b.get(key), key
    # cloud_resource is the ninth key — _entities does not read it.
    assert "cloud_resource" not in recovered


def test_as_raw_round_trips_for_non_ip_keys_only():
    b = resolve_stitch_context(FULL_SPEC, seed="run-RAW")
    recovered = _entities(result=b.as_raw(), observation=None)
    # These raw keys ARE in _entities' pick lists.
    for key in ("host", "src_port", "dst_port", "protocol", "container_id", "account"):
        assert recovered[key] == b.get(key), key
    # But _entities never reads emitter-native 'src'/'dst' → the IP legs are lost.
    assert recovered["src_ip"] is None
    assert recovered["dst_ip"] is None


# ---------------------------------------------------------------------------
# Per-channel projections — exact key spellings
# ---------------------------------------------------------------------------


def test_as_raw_keys_and_ip_spelling():
    b = resolve_stitch_context(FULL_SPEC, seed="run-1")
    raw = b.as_raw()
    assert raw["src"] == b.src_ip
    assert raw["dst"] == b.dst_ip
    assert raw["proto"] == "tcp"
    assert raw["host"] == "jumpbox-01"


def test_as_xdr_columns_keys():
    b = resolve_stitch_context(FULL_SPEC, seed="run-1")
    cols = b.as_xdr_columns()
    assert cols["agent_hostname"] == "jumpbox-01"
    assert cols["source_ip"] == b.src_ip
    assert cols["dest_ip"] == "203.0.113.10"
    assert cols["actor_effective_user_name"] == b.account
    assert cols["resource_name"] == "arn:aws:s3:::acme-logs"


def test_as_xdm_paths():
    b = resolve_stitch_context(FULL_SPEC, seed="run-1")
    xdm = b.as_xdm()
    assert xdm["xdm.source.host.hostname"] == "jumpbox-01"
    assert xdm["xdm.source.ipv4"] == b.src_ip
    assert xdm["xdm.target.ipv4"] == "203.0.113.10"
    assert xdm["xdm.target.port"] == 443
    assert xdm["xdm.source.user.username"] == b.account
    assert xdm["xdm.target.resource.name"] == "arn:aws:s3:::acme-logs"


def test_projections_omit_unresolved_keys():
    b = resolve_stitch_context({"dst_port": {"literal": 443}}, seed="run-1")
    assert b.as_raw() == {"dst_port": 443}
    assert b.as_xdr_columns() == {"dest_port": 443}
    assert b.as_xdm() == {"xdm.target.port": 443}


def test_values_holds_all_nine_keys_with_none_for_absent():
    b = resolve_stitch_context({"dst_port": {"literal": 443}}, seed="run-1")
    assert set(b.values) == set(ENTITY_KEYS)
    assert b.values["dst_port"] == 443
    assert b.values["src_ip"] is None


def test_get_unknown_key_returns_none():
    b = resolve_stitch_context({"dst_port": {"literal": 443}}, seed="run-1")
    assert b.get("nonsense") is None


# ---------------------------------------------------------------------------
# Derivation shapes
# ---------------------------------------------------------------------------


def test_auto_ip_is_lab_range_avoiding_0_and_255():
    b = resolve_stitch_context({"src_ip": {"resolve": "auto_ip"}}, seed="run-1")
    octets = b.src_ip.split(".")
    assert octets[0] == "10"
    for o in octets[1:]:
        assert 1 <= int(o) <= 254


def test_auto_port_is_ephemeral_range():
    b = resolve_stitch_context({"src_port": {"resolve": "auto_port"}}, seed="run-1")
    assert 49152 <= b.src_port <= 65535


def test_auto_ip_on_src_and_dst_differ_under_one_seed():
    b = resolve_stitch_context(
        {"src_ip": {"resolve": "auto_ip"}, "dst_ip": {"resolve": "auto_ip"}},
        seed="run-1",
    )
    assert b.src_ip != b.dst_ip


# ---------------------------------------------------------------------------
# auto_5tuple — one key fills all five, coherently and idempotently
# ---------------------------------------------------------------------------


def test_auto_5tuple_on_one_key_fills_all_five():
    b = resolve_stitch_context({"src_ip": {"resolve": "auto_5tuple"}}, seed="run-1")
    for k in ("src_ip", "src_port", "dst_ip", "dst_port", "protocol"):
        assert b.get(k) is not None, k
    assert b.protocol == "tcp"
    # Keys outside the tuple stay unset.
    assert b.host is None
    assert b.container_id is None


def test_auto_5tuple_idempotent_across_several_keys():
    one = resolve_stitch_context({"src_ip": {"resolve": "auto_5tuple"}}, seed="run-1")
    many = resolve_stitch_context(
        {
            "src_ip": {"resolve": "auto_5tuple"},
            "dst_port": {"resolve": "auto_5tuple"},
            "protocol": {"resolve": "auto_5tuple"},
        },
        seed="run-1",
    )
    assert one.values == many.values


def test_auto_5tuple_does_not_override_an_explicit_literal():
    b = resolve_stitch_context(
        {
            "src_ip": {"resolve": "auto_5tuple"},
            "dst_ip": {"literal": "203.0.113.99"},
        },
        seed="run-1",
    )
    assert b.dst_ip == "203.0.113.99"  # literal wins
    assert b.src_ip is not None  # tuple still filled the rest


# ---------------------------------------------------------------------------
# canary_principal — delegated to analytics_emitter.canary_bindings
# ---------------------------------------------------------------------------


def test_canary_principal_delegates_to_canary_bindings():
    b = resolve_stitch_context({"account": {"resolve": "canary_principal"}}, seed="run-C")
    # Whatever token the resolver derived, account/principal must match exactly
    # what canary_bindings yields for it (identity leg not forked).
    token = b.account
    expected = canary_bindings(token)
    assert b.account == expected["account"]
    assert b.principal == expected["principal"]
    assert b.principal.endswith("@cortexsim-canary.invalid")


def test_canary_principal_deterministic_token():
    a = resolve_stitch_context({"account": {"resolve": "canary_principal"}}, seed="run-C")
    b = resolve_stitch_context({"account": {"resolve": "canary_principal"}}, seed="run-C")
    assert a.account == b.account


# ---------------------------------------------------------------------------
# from_agent — reads the launch target, sentinel on absence
# ---------------------------------------------------------------------------


def test_from_agent_host_uses_target_dict_hostname():
    b = resolve_stitch_context(
        {"host": {"resolve": "from_agent"}},
        seed="run-1",
        target={"hostname": "prod-web-7", "ip": "10.20.30.40"},
    )
    assert b.host == "prod-web-7"


def test_from_agent_src_ip_uses_target_ip():
    b = resolve_stitch_context(
        {"src_ip": {"resolve": "from_agent"}},
        seed="run-1",
        target={"hostname": "prod-web-7", "ip": "10.20.30.40"},
    )
    assert b.src_ip == "10.20.30.40"


def test_from_agent_reads_orm_shaped_target():
    class _Agent:
        hostname = "orm-host-9"
        ip = None

    b = resolve_stitch_context(
        {"host": {"resolve": "from_agent"}}, seed="run-1", target=_Agent()
    )
    assert b.host == "orm-host-9"


def test_from_agent_host_sentinel_when_target_absent():
    b = resolve_stitch_context({"host": {"resolve": "from_agent"}}, seed="run-1")
    # A clearly-synthetic sentinel, deterministic, never a fabricated real name.
    assert b.host.startswith("cortexsim-target-")
    again = resolve_stitch_context({"host": {"resolve": "from_agent"}}, seed="run-1")
    assert b.host == again.host


def test_from_agent_src_ip_sentinel_is_lab_range_when_target_absent():
    b = resolve_stitch_context({"src_ip": {"resolve": "from_agent"}}, seed="run-1")
    assert b.src_ip.startswith("10.")


# ---------------------------------------------------------------------------
# Fail-closed rejection
# ---------------------------------------------------------------------------


def test_unknown_directive_raises():
    with pytest.raises(StitchContextValidationError) as ei:
        resolve_stitch_context({"src_ip": {"resolve": "auto_teleport"}}, seed="run-1")
    assert ei.value.key == "src_ip"
    assert ei.value.directive == "auto_teleport"
    assert ei.value.code == "STITCH_CONTEXT_INVALID"


def test_incompatible_directive_on_key_raises():
    # auto_5tuple is rejected on host (not a tuple key).
    with pytest.raises(StitchContextValidationError) as ei:
        resolve_stitch_context({"host": {"resolve": "auto_5tuple"}}, seed="run-1")
    assert ei.value.key == "host"
    assert ei.value.directive == "auto_5tuple"


def test_canary_principal_on_wrong_key_raises():
    with pytest.raises(StitchContextValidationError):
        resolve_stitch_context({"host": {"resolve": "canary_principal"}}, seed="run-1")


def test_unknown_entity_key_raises():
    with pytest.raises(StitchContextValidationError) as ei:
        resolve_stitch_context({"floccinaucinihil": {"literal": "x"}}, seed="run-1")
    assert ei.value.key == "floccinaucinihil"


def test_both_literal_and_resolve_raises():
    with pytest.raises(StitchContextValidationError) as ei:
        resolve_stitch_context(
            {"src_ip": {"literal": "1.2.3.4", "resolve": "auto_ip"}}, seed="run-1"
        )
    assert ei.value.key == "src_ip"


def test_neither_literal_nor_resolve_raises():
    with pytest.raises(StitchContextValidationError) as ei:
        resolve_stitch_context({"src_ip": {}}, seed="run-1")
    assert ei.value.key == "src_ip"


def test_non_object_entry_raises():
    with pytest.raises(StitchContextValidationError):
        resolve_stitch_context({"src_ip": "1.2.3.4"}, seed="run-1")


def test_directive_compat_covers_every_directive():
    # Guard: DIRECTIVE_COMPAT must have an entry for every shipped directive.
    assert set(DIRECTIVE_COMPAT) == DIRECTIVES
