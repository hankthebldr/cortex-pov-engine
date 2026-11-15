"""#346: a BIOC row and an Analytics BIOC alert are one behavioural family.

The tenant raises "XDR Analytics BIOC" where a card declares BIOC, or "XDR
BIOC" where it declares ABIOC, for the same behaviour: the label is PANW's
packaging, not something a card author can know. The family gate dropped
every such alert before a key was looked at.

The decision: the two labels may cross, on an identity key only (the exact
technique, the rule name, the detection id), and one firing is never counted
twice. Every other pair of families stays a disagreement: an agent module's
alert is not a rule.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytest

from connectors.base import ObservedAlert
from connectors.matcher import CROSS_FAMILY, reconcile

T0 = datetime(2026, 11, 15, 6, 6, 30)


class Row:
    def __init__(self, rid: int, signal_type: str, *, technique: str = "T1059.004",
                 name: Optional[str] = None, detection_id: Optional[str] = None,
                 expected: str = "expected detection", observed: bool = False):
        self.id = rid
        self.signal_type = signal_type
        self.mitre_technique = technique
        self.detection_name = name
        self.detection_id = detection_id
        self.expected_detection = expected
        self.executed_at = T0
        self.observed = observed


def alert(source: str, *, techniques=("T1059.004",), name: str = "tenant alert",
          ext: str = "A1", seconds: int = 35, detection_id: Optional[str] = None):
    return ObservedAlert(source="xsiam", observed_at=T0 + timedelta(seconds=seconds),
                         external_id=ext, name=name, techniques=list(techniques),
                         alert_source=source, detection_id=detection_id)


# ── the labels cross on an identity key ─────────────────────────────────────

def test_an_analytics_bioc_alert_credits_a_bioc_row_on_the_exact_technique():
    v = reconcile([Row(1, "BIOC")], [alert("XDR Analytics BIOC")])[0]
    assert v.matched
    assert v.matched_on == ["technique", CROSS_FAMILY]
    assert v.matched_via == "abioc"
    assert v.alert_source == "XDR Analytics BIOC"
    assert v.to_dict()["matched_via"] == "abioc"


def test_a_bioc_alert_credits_an_abioc_row_on_the_exact_technique():
    v = reconcile([Row(1, "ABIOC")], [alert("XDR BIOC")])[0]
    assert v.matched and v.matched_via == "bioc"


def test_the_rule_name_crosses_the_label():
    row = Row(1, "BIOC", technique="T1110.004", name="Suspicious sshpass command execution")
    a = alert("XDR Analytics BIOC", techniques=(), name="Suspicious sshpass command execution")
    v = reconcile([row], [a])[0]
    assert v.matched and v.matched_on == ["rule_name", CROSS_FAMILY] and v.matched_via == "abioc"


def test_the_detection_id_crosses_the_label():
    row = Row(1, "ABIOC", technique="T1136.001",
              detection_id="abioc-suspicious-access-to-shadow-file")
    a = alert("XDR BIOC", techniques=(), detection_id="abioc-suspicious-access-to-shadow-file")
    v = reconcile([row], [a])[0]
    assert v.matched and "detection_id" in v.matched_on and v.matched_via == "bioc"


def test_the_issues_api_method_codes_cross_too():
    v = reconcile([Row(1, "BIOC")], [alert("ANALYTICS_BIOC")])[0]
    assert v.matched and v.matched_via == "abioc"


# ── ... and on nothing weaker ───────────────────────────────────────────────

def test_a_shared_base_technique_does_not_cross_the_label():
    """SIM-MP-003's "A TCP stream was created directly in a shell" is T1059;
    a BIOC row on T1059.004 is a different behaviour until something says so."""
    v = reconcile([Row(1, "BIOC", technique="T1059.004")],
                  [alert("XDR Analytics BIOC", techniques=("T1059",))])[0]
    assert not v.matched


def test_shared_name_words_do_not_cross_the_label():
    row = Row(1, "BIOC", technique="T1003", expected="Dumping registry hives with reg save")
    a = alert("XDR Analytics BIOC", techniques=("T1552",),
              name="Dumping Registry hives with passwords")
    assert not reconcile([row], [a])[0].matched


def test_a_same_label_match_still_takes_base_technique_and_name():
    """The strict keys are for crossing only: inside one label nothing changed."""
    v = reconcile([Row(1, "BIOC", technique="T1059.004")],
                  [alert("XDR BIOC", techniques=("T1059",))])[0]
    assert v.matched and v.matched_on == ["technique-base", "source"]
    assert v.matched_via is None


# ── one firing is never counted twice ───────────────────────────────────────

def test_an_alert_the_run_expects_under_its_own_label_does_not_cross():
    """A step expecting a BIOC and an ABIOC on one technique: one BIOC firing
    satisfies the BIOC row, as before, and not the ABIOC row beside it."""
    rows = [Row(1, "BIOC"), Row(2, "ABIOC")]
    by = {v.result_id: v for v in reconcile(rows, [alert("XDR BIOC")])}
    assert by[1].matched and by[1].matched_via is None
    assert not by[2].matched


def test_a_row_the_alert_credited_in_an_earlier_reconcile_still_blocks_the_crossing():
    """The BIOC row is already observed, so it is no longer a candidate; the
    alert that credited it must not now cross to the ABIOC row."""
    rows = [Row(1, "BIOC", observed=True), Row(2, "ABIOC")]
    verdicts = reconcile(rows, [alert("XDR BIOC")])
    assert [v.result_id for v in verdicts] == [2] and not verdicts[0].matched


def test_one_crossing_alert_credits_one_row():
    rows = [Row(1, "ABIOC"), Row(2, "ABIOC")]
    by = {v.result_id: v for v in reconcile(rows, [alert("XDR BIOC")])}
    assert by[1].matched and not by[2].matched


def test_two_crossing_alerts_credit_two_rows():
    rows = [Row(1, "ABIOC"), Row(2, "ABIOC")]
    alerts = [alert("XDR BIOC", ext="A1", seconds=30), alert("XDR BIOC", ext="A2", seconds=40)]
    by = {v.result_id: v for v in reconcile(rows, alerts)}
    assert (by[1].alert_external_id, by[2].alert_external_id) == ("A1", "A2")


def test_an_alert_credited_in_an_earlier_reconcile_does_not_cross_again():
    """Consumption lasts one pass. The service names the alerts already on
    record against a row, so the next pass cannot hand A1 to the next row."""
    v = reconcile([Row(2, "ABIOC")], [alert("XDR BIOC", ext="A1")],
                  already_credited={"A1"})[0]
    assert not v.matched


# ── every other pair of families is still a disagreement ────────────────────

@pytest.mark.parametrize("row_type, source", [
    ("BIOC", "XDR Agent"),              # an agent module is not a rule
    ("ABIOC", "XDR Agent"),
    ("Prevention", "XDR BIOC"),
    ("Correlation", "XDR Analytics BIOC"),
    ("XQL", "XDR BIOC"),
    ("IOC", "XDR Analytics BIOC"),
    ("ABIOC", "XDR Analytics"),         # MAGNIFIER: analytics, not an Analytics BIOC
    ("Analytics", "XDR BIOC"),
])
def test_other_family_pairs_never_match(row_type, source):
    v = reconcile([Row(1, row_type, name="tenant alert")], [alert(source)])[0]
    assert not v.matched, (row_type, source)
