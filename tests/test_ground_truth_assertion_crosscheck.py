"""tests/test_ground_truth_assertion_crosscheck.py

Guard for DOCS-17: ``docs/reference/ground-truth.md`` printed two different
answers to "how many PLT / POS assertions are authored" on one page.

  * its *Assertions* section, from the real ``AssertionCatalog``:
    ``{"AUT": 3, "PLT": 7, "POS": 18}``;
  * its *UC/TC index* section, from ``uctc_crosswalk_v2.2.py --report``, which
    counted two hand-maintained dicts (``PLT_ASSERTION_CROSSWALK`` /
    ``POS_ASSERTION_CROSSWALK``): ``PLT 4/43 rows (4 artifacts)`` and
    ``POS 2/110 rows (2 artifacts)``, and no AUT line at all.

``generate_ground_truth.py`` cross-checks every number two independent methods
can reach, but never compared those two, so the page shipped both. The fix has
two halves and this file guards both:

  1. the crosswalk derives the authored rows from the loader, not a hand table;
  2. ``generate_ground_truth.cross_check_assertion_counts`` FAILS when the
     crosswalk's per-class artifact counts disagree with the loader's.

``test_crosswalk_and_loader_agree_on_this_tree`` is the one that was observed
red on the unfixed base (cc26ddf): PLT 4 != 7, POS 2 != 18, AUT absent != 3.
The two ``*_raises`` tests are the negative controls that keep the check able
to fail at all — a cross-check that can never raise proves nothing.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "generate_ground_truth.py"
CROSSWALK_SCRIPT = REPO_ROOT / "scripts" / "uctc_crosswalk_v2.2.py"


def _load_script(path: Path, name: str):
    # Load by path under a private module name: scripts/ is not a package, and
    # a shared name would collide with any other test that imports the script.
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gt():
    return _load_script(SCRIPT, "_gt_assertion_crosscheck")


@pytest.fixture(scope="module")
def xw():
    return _load_script(CROSSWALK_SCRIPT, "_uctc_crosswalk_assertion_crosscheck")


def test_crosswalk_and_loader_agree_on_this_tree(gt):
    """The crosswalk's per-class assertion counts equal the real loader's."""
    by_class = gt.cross_check_assertion_counts()

    # A zero is degraded, not ok: an empty agreement (both sides read nothing)
    # would pass the equality check while proving nothing about the corpus.
    assert by_class, "cross-check returned no classes at all"
    assert set(by_class) == {"AUT", "PLT", "POS"}, by_class
    assert all(n > 0 for n in by_class.values()), by_class

    # Independent third method: a filesystem glob of assertions/{pos,plt,aut}.
    fs = gt.count_assertions_filesystem()["by_validation_class"]
    assert by_class == fs, (by_class, fs)


def test_cross_check_raises_on_the_base_tree_numbers(gt):
    """Negative control: the exact disagreement ground-truth.md shipped."""
    with pytest.raises(gt.GroundTruthError) as exc:
        gt.cross_check_assertion_classes(
            {"PLT": 4, "POS": 2},
            {"AUT": 3, "PLT": 7, "POS": 18},
            loader_label="the real AssertionCatalog",
        )
    msg = str(exc.value)
    assert "PLT" in msg and "4" in msg and "7" in msg, msg
    assert "POS" in msg and "2" in msg and "18" in msg, msg
    # A class the loader carries that the crosswalk never prints is a
    # mismatch, not an implicit zero.
    assert "AUT" in msg, msg


def test_cross_check_raises_when_crosswalk_reports_a_class_the_loader_lacks(gt):
    with pytest.raises(gt.GroundTruthError):
        gt.cross_check_assertion_classes(
            {"AUT": 0, "PLT": 7, "POS": 18, "DET": 1},
            {"PLT": 7, "POS": 18},
            loader_label="the real AssertionCatalog",
        )


def test_uctc_parser_refuses_a_report_without_the_aut_line(gt):
    """An unrecognised shape must raise rather than read as empty: the base
    report (PLT + POS lines only) no longer parses."""
    base_report = "\n".join([
        "scenarios: 193   crosswalk rows: 193",
        "resolution: {'REMAP': 172, 'NET-NEW': 21}",
        "index TCs evidenced: 90/266  (DET/HNT 70/107)",
        "payload resolved: 193 | unresolved: 0",
        "tier drift (S-13): 124",
        "posture-class primary (S-14): 14",
        "proposed v2.3 TCs: 17 {'UC-BROWSER': 6}",
        "PLT assertions: 4 artifact(s) -> 4/43 PLT rows AUTHORED | 0/43 PROVEN "
        "(no tenant run recorded) | 9 documented non-bindings",
        "POS assertions (K8s pack): 2 artifact(s) -> 2/110 POS rows AUTHORED | "
        "0/110 PROVEN (no tenant run recorded) | net-new ['TC-KSPM-03'] | "
        "breadth-not-coverage ['TC-CSPM-02'] | 4 documented non-bindings",
    ]) + "\n"
    with pytest.raises(gt.GroundTruthError):
        gt.parse_uctc_report(base_report)


# --------------------------------------------------------------------------
# The crosswalk side: authored rows come from the loader, and the narrative
# dicts that remain may never contradict it.
# --------------------------------------------------------------------------

def test_crosswalk_bindings_come_from_the_loader(xw, gt):
    bindings = xw.load_assertion_bindings()
    per_class = {cls: len(ids) for cls, ids in bindings.items()}
    assert per_class == gt.count_assertions_filesystem()["by_validation_class"]


def test_crosswalk_narrative_agrees_with_the_loaded_corpus(xw):
    tc, _spec, _lib = xw.load_index()
    assert xw.check_assertion_narrative(xw.load_assertion_bindings(), tc) == []


def test_narrative_check_flags_a_closed_gap_listed_as_open(xw, monkeypatch):
    """Negative control: TC-NDR-05 sat in PLT_NOT_BOUND ("cannot be
    exercised") while PLT-NDR-005 bound it. Putting it back must be caught."""
    tc, _spec, _lib = xw.load_index()
    monkeypatch.setitem(xw.PLT_NOT_BOUND, "TC-NDR-05", "stale reason")
    problems = xw.check_assertion_narrative(xw.load_assertion_bindings(), tc)
    assert any("TC-NDR-05" in p and "PLT-NDR-005" in p for p in problems), problems


def test_narrative_check_flags_an_entry_for_an_artifact_that_does_not_load(xw, monkeypatch):
    tc, _spec, _lib = xw.load_index()
    monkeypatch.setitem(xw.POS_ASSERTION_CROSSWALK, "POS-NOPE-999",
                        (["TC-KSPM-03"], "mechanism", "not proven"))
    problems = xw.check_assertion_narrative(xw.load_assertion_bindings(), tc)
    assert any("POS-NOPE-999" in p for p in problems), problems


def test_narrative_check_flags_wrong_test_case_binding(xw, monkeypatch):
    tc, _spec, _lib = xw.load_index()
    refs, mech, not_proven = xw.PLT_ASSERTION_CROSSWALK["PLT-IR-008"]
    monkeypatch.setitem(xw.PLT_ASSERTION_CROSSWALK, "PLT-IR-008",
                        (["TC-ERV-01"], mech, not_proven))
    problems = xw.check_assertion_narrative(xw.load_assertion_bindings(), tc)
    assert any("PLT-IR-008" in p and "TC-IR-08" in p for p in problems), problems


def test_crosswalk_refuses_to_count_without_the_loader(xw, monkeypatch):
    """No loader means no count — never a silent zero."""
    import sys
    monkeypatch.setitem(sys.modules, "engine.assertions", None)
    with pytest.raises(xw.AssertionBindingError, match="AssertionCatalog"):
        xw.load_assertion_bindings()


def test_crosswalk_refuses_to_count_a_rejected_artifact(xw, monkeypatch, tmp_path):
    """A rejected artifact is not authored evidence; counting around it would
    understate the tree, so the report refuses instead."""
    (tmp_path / "assertions" / "plt").mkdir(parents=True)
    (tmp_path / "assertions" / "plt" / "PLT-BAD-001.yml").write_text(
        "assertion_id: PLT-BAD-001\nthis: [is not a valid artifact\n", encoding="utf-8")
    import sys
    monkeypatch.setattr(sys, "path", list(sys.path))  # undo the tmp core/ insert
    monkeypatch.setattr(xw, "REPO", str(tmp_path))
    with pytest.raises(xw.AssertionBindingError, match="rejected"):
        xw.load_assertion_bindings()
