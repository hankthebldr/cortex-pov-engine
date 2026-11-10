"""Guards for the product-mechanics ledger (scripts/product_mechanics.py).

The load-bearing guard is the confidentiality boundary: an answer whose source is
not ``distribution: public`` must refuse the whole file, because this repo is
public and the vault forbids internal-only PANW material on both Macs.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "product_mechanics.py"


def _load():
    spec = importlib.util.spec_from_file_location("product_mechanics", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pm = _load()


def _ledger() -> dict:
    return {
        "schema_version": 1,
        "updated": "2026-11-05",
        "mechanisms": [
            {"id": "STI", "name": "Stitching", "summary": "Links records across sensors.",
             "known": [], "assumes": [{"assumption": "5-tuple join.", "ref": "n/a"}],
             "contrasts": [{"vs": "INC", "point": "Records vs alerts."}]},
            {"id": "INC", "name": "Incidents", "summary": "Groups alerts.",
             "known": [], "assumes": [], "contrasts": []},
        ],
        "questions": [
            {"id": "PM-STI-01", "mechanism": "STI", "priority": "P0", "status": "open",
             "question": "Which fields?", "context": "Because.", "lever": "Emitter fields.",
             "affects": ["ngfw_eal_emitter"],
             "decides": [{"if": "full tuple", "then": "share src_port"}],
             "experiment": "Off-by-one src port."},
            {"id": "PM-INC-01", "mechanism": "INC", "priority": "P1", "status": "open",
             "question": "What groups alerts?", "context": "KPI.", "lever": "Shared entity.",
             "affects": ["measures"]},
        ],
    }


def _answer_file(records_yaml: str, packet: str = "PMQ-2026-11-05-t") -> str:
    return (f"---\npacket: {packet}\nanswered: 2026-11-06\nanswered_by: test\n---\n\n"
            f"```yaml\nanswers:\n{records_yaml}```\n")


PUBLIC_ANSWER = """\
  - id: PM-STI-01
    status: answered
    answer: Full 5-tuple.
    rule: Share src_port.
    sources:
      - kind: public_doc
        title: Stitching
        ref: https://cortex-docs.paloaltonetworks.com/x
        date: 2026-11-06
        distribution: public
"""

IDS = {"PM-STI-01", "PM-INC-01"}


# ── the real ledger ─────────────────────────────────────────────────────────

def test_real_ledger_validates_and_rendered_view_is_fresh():
    assert pm.main(["check"]) == 0


def test_real_ledger_every_question_names_a_lever_and_a_touched_artifact():
    data = pm.load_ledger()
    for q in data["questions"]:
        assert q["lever"].strip() and q["affects"], q["id"]


# ── ledger shape ────────────────────────────────────────────────────────────

def test_valid_fixture_ledger_passes():
    pm.validate_ledger(_ledger())


def test_duplicate_question_id_raises():
    data = _ledger()
    data["questions"].append(copy.deepcopy(data["questions"][0]))
    with pytest.raises(pm.LedgerError, match="duplicate"):
        pm.validate_ledger(data)


def test_question_id_prefix_must_match_mechanism():
    data = _ledger()
    data["questions"][0]["mechanism"] = "INC"
    with pytest.raises(pm.LedgerError, match="disagrees"):
        pm.validate_ledger(data)


def test_unknown_key_raises_rather_than_being_ignored():
    data = _ledger()
    data["questions"][0]["threshold"] = 5
    with pytest.raises(pm.LedgerError, match="unknown keys"):
        pm.validate_ledger(data)


def test_ref_to_a_missing_repo_path_raises():
    data = _ledger()
    data["mechanisms"][0]["assumes"][0]["ref"] = "core/engine/no_such_module.py:12"
    with pytest.raises(pm.LedgerError, match="does not exist"):
        pm.validate_ledger(data)


def test_known_fact_with_an_internal_source_kind_raises():
    data = _ledger()
    data["mechanisms"][0]["known"] = [
        {"fact": "x", "source": {"kind": "confluence", "ref": "wiki", "date": "2026-11-05"}}]
    with pytest.raises(pm.LedgerError, match="is not one of"):
        pm.validate_ledger(data)


# ── answers: the confidentiality boundary ───────────────────────────────────

def test_public_answer_file_parses():
    parsed = pm.parse_answer_file(_answer_file(PUBLIC_ANSWER), IDS)
    assert parsed["answers"][0]["rule"] == "Share src_port."


def test_internal_distribution_refuses_the_whole_file():
    text = _answer_file(PUBLIC_ANSWER.replace("distribution: public", "distribution: internal"))
    with pytest.raises(pm.ConfidentialityError):
        pm.parse_answer_file(text, IDS)


def test_internal_source_kind_cannot_cross_even_if_marked_public():
    text = _answer_file(PUBLIC_ANSWER.replace("kind: public_doc", "kind: jira"))
    with pytest.raises(pm.LedgerError, match="may not cross"):
        pm.parse_answer_file(text, IDS)


def test_internal_only_answer_must_carry_no_content():
    rec = "  - id: PM-STI-01\n    status: internal_only\n    answer: the threshold is 7\n"
    with pytest.raises(pm.LedgerError, match="stay on the work Mac"):
        pm.parse_answer_file(_answer_file(rec), IDS)


def test_bare_internal_only_answer_is_accepted():
    rec = "  - id: PM-STI-01\n    status: internal_only\n"
    assert pm.parse_answer_file(_answer_file(rec), IDS)["answers"][0]["status"] == "internal_only"


def test_long_excerpt_raises():
    long = " ".join(["word"] * 26)
    text = _answer_file(PUBLIC_ANSWER + f"        excerpt: {long}\n")
    with pytest.raises(pm.LedgerError, match="at most 25"):
        pm.parse_answer_file(text, IDS)


def test_answer_for_a_question_not_in_the_ledger_raises():
    with pytest.raises(pm.LedgerError, match="not a ledger question"):
        pm.parse_answer_file(_answer_file(PUBLIC_ANSWER.replace("PM-STI-01", "PM-STI-99")), IDS)


def test_answered_without_sources_raises():
    rec = "  - id: PM-STI-01\n    status: answered\n    answer: It does.\n"
    with pytest.raises(pm.LedgerError, match="needs at least one source"):
        pm.parse_answer_file(_answer_file(rec), IDS)


def test_answer_file_needs_exactly_one_yaml_block():
    text = _answer_file(PUBLIC_ANSWER) + "\n```yaml\nanswers: []\n```\n"
    with pytest.raises(pm.LedgerError, match="exactly one"):
        pm.parse_answer_file(text, IDS)


# ── status lifecycle ────────────────────────────────────────────────────────

def test_status_moves_open_to_asked_to_answered():
    q = _ledger()["questions"][0]
    assert pm.effective_status(q, [], []) == "open"
    assert pm.effective_status(q, [], ["PMQ-2026-11-05-t"]) == "asked"
    rec = pm.parse_answer_file(_answer_file(PUBLIC_ANSWER), IDS)["answers"]
    assert pm.effective_status(q, rec, ["PMQ-2026-11-05-t"]) not in ("open", "asked")


def test_only_internal_answers_read_internal_only():
    q = _ledger()["questions"][0]
    assert pm.effective_status(q, [{"status": "internal_only"}], []) == "internal_only"


def test_retired_wins():
    q = dict(_ledger()["questions"][0], status="retired")
    assert pm.effective_status(q, [{"status": "internal_only"}], []) == "retired"


# ── packets ─────────────────────────────────────────────────────────────────

def test_packet_is_self_contained_and_carries_the_rule():
    data = pm.validate_ledger(_ledger())
    qs = pm.select_questions(data, {}, {}, priorities=["P0"])
    text = pm.render_packet(data, qs, "PMQ-2026-11-05-t", "2026-11-05")
    assert [q["id"] for q in qs] == ["PM-STI-01"]
    assert "PM-STI-01" in text and "PM-INC-01" not in text
    assert "_RoB/product-mechanics/answers/PMA-2026-11-05-t.md" in text
    assert "**public**" in text and "internal_only" in text and "outside the vault" in text
    assert text == pm.render_packet(data, qs, "PMQ-2026-11-05-t", "2026-11-05")


def test_selection_skips_asked_unless_told():
    data = pm.validate_ledger(_ledger())
    asked = {"PM-STI-01": ["PMQ-x"]}
    assert [q["id"] for q in pm.select_questions(data, {}, asked)] == ["PM-INC-01"]
    got = pm.select_questions(data, {}, asked, include_asked=True)
    assert [q["id"] for q in got] == ["PM-STI-01", "PM-INC-01"]


def test_empty_selection_raises():
    data = pm.validate_ledger(_ledger())
    with pytest.raises(pm.LedgerError, match="no questions"):
        pm.select_questions(data, {}, {}, mechanisms=["COR"])


# ── vault ───────────────────────────────────────────────────────────────────

def test_vault_root_refuses_to_guess():
    with pytest.raises(pm.LedgerError, match="OBSIDIAN_VAULT_NAME"):
        pm.vault_root({"OBSIDIAN_ROOT": "/tmp"})
