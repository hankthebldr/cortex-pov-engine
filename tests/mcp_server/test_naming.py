"""Canonical ids and the one derived wire-name mapping (spec §5.1, Appendix A)."""
import pytest

from mcp_server.naming import (
    CANONICAL_IDS,
    GROUP,
    WIRE_PATTERN,
    canonical_id,
    group_of,
    wire_name,
)


def test_every_canonical_id_round_trips_and_fits():
    for cid in CANONICAL_IDS:
        wire = wire_name(cid)
        assert canonical_id(wire) == cid
        assert WIRE_PATTERN.match(wire)
        assert len(wire) <= 48


def test_there_are_52_ids_27_cortexsim_25_xsiam():
    assert len(CANONICAL_IDS) == 52
    assert len(set(CANONICAL_IDS)) == 52
    assert sum(group_of(i) == "cortexsim" for i in CANONICAL_IDS) == 27
    assert sum(group_of(i) == "xsiam" for i in CANONICAL_IDS) == 25
    assert GROUP == {"cortexsim", "xsiam"}
    assert CANONICAL_IDS[0] == "cortexsim.health.get"
    assert CANONICAL_IDS[-1] == "xsiam.assessment_result.list"


def test_longest_wire_name_is_34_chars():
    assert max(len(wire_name(i)) for i in CANONICAL_IDS) == 34


def test_no_wire_name_carries_a_dot_and_unknown_wire_is_rejected():
    assert all("." not in wire_name(i) for i in CANONICAL_IDS)
    with pytest.raises(ValueError):
        canonical_id("no_such_tool")
    with pytest.raises(ValueError):
        wire_name("Bad.Id")
    with pytest.raises(ValueError):
        group_of("other.thing.get")
