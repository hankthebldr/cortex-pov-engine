"""The Composer's fork, as the drafts-API tests see it — from a golden pair.

When a DC opens a corpus scenario in the Composer and saves it, the console
runs ``draftFromScenario`` then ``draftToApi``
(``ui/src/components/console/composerDraft.js``) over the detail document
``GET /api/scenarios/{id}`` returned, and POSTs the result. The backend suite
cannot run that JavaScript (the CI ``backend`` job runs in the prod image,
which ships no node), and a hand-written Python mirror of it drifted silently
once already: by 2026-11-11 it omitted sixteen fields the console had started
carrying. So nothing here re-implements the fork. Instead a golden pair is
committed under ``tests/fixtures/composer_fork/`` and pinned from both sides:

* ``SIM-MP-031.doc.json`` — the detail document, i.e. the real YAML through
  the real strict loader into a ``Scenario`` row and ``to_dict()`` (minus the
  keys in ``_VOLATILE``, which the fork never reads). ``fork_body_like_the_ui``
  refuses to return the body unless this still equals the live corpus.
* ``SIM-MP-031.body.json`` — ``draftToApi(draftFromScenario(doc))`` computed
  by the real JS. ``ui/src/components/__tests__/composerForkGolden.test.js``
  fails if the JS stops producing exactly this.

Either side changing turns one suite red and names the regeneration command,
so the body the drafts-API tests POST is always the body the console sends.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = REPO_ROOT / "scenarios"
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "composer_fork"

#: Keys of the detail document the fork never reads, excluded from the fixture
#: because they would make the drift check flaky rather than meaningful:
#: ``created_at`` changes every run, and the two entitlement lists are derived
#: from the UC/TC registry, a process-wide singleton that is empty or loaded
#: depending on which tests ran first. ``draftToApi`` omits both lists; the
#: server derives them.
_VOLATILE = ("created_at", "required_addons", "required_base_platform")

REGENERATE = """\
  # 1. the document, from the live corpus
  PYTHONPATH=core .venv/bin/python -c "import json; from tests._composer_fork import \\
    corpus_doc_for_fixture as d; json.dump(d('{sid}'), open('tests/fixtures/composer_fork/{sid}.doc.json','w'), \\
    indent=2, sort_keys=True, ensure_ascii=False)"
  # 2. the body, from the real JS — see the header of composerForkGolden.test.js
"""


def load_corpus_doc(scenario_id: str) -> dict[str, Any]:
    """The detail document the Composer fetches for ``scenario_id``: the real
    YAML through the real strict loader into a ``Scenario`` row, then
    ``to_dict()``. Raises rather than returning a fabricated scenario."""
    from engine.scenario_loader import (  # noqa: PLC0415
        _find_yaml_files,
        _parse_and_validate,
        _schema_to_orm_kwargs,
    )
    from models import Scenario  # noqa: PLC0415

    needle = f"scenario_id: {scenario_id}"
    for path in _find_yaml_files(str(SCENARIOS_DIR)):
        if needle not in Path(path).read_text(encoding="utf-8"):
            continue
        schema, err = _parse_and_validate(path)
        if err is not None or schema is None:
            raise AssertionError(f"{path} failed the strict loader: {err}")
        if schema.scenario_id == scenario_id:
            return Scenario(**_schema_to_orm_kwargs(schema)).to_dict()
    raise LookupError(f"{scenario_id} is not in the corpus under {SCENARIOS_DIR}")


def corpus_doc_for_fixture(scenario_id: str) -> dict[str, Any]:
    """``load_corpus_doc`` minus the volatile keys, JSON-normalised — the exact
    form committed as ``<id>.doc.json``."""
    doc = load_corpus_doc(scenario_id)
    for key in _VOLATILE:
        doc.pop(key, None)
    return json.loads(json.dumps(doc, sort_keys=True, ensure_ascii=False))


def fork_body_like_the_ui(scenario_id: str) -> dict[str, Any]:
    """The body the console POSTs when a DC forks ``scenario_id``.

    Read from the golden pair, after proving its document half still matches
    the live corpus — a stale fixture fails loudly instead of testing a
    scenario that no longer exists in that form."""
    doc_path = FIXTURES / f"{scenario_id}.doc.json"
    body_path = FIXTURES / f"{scenario_id}.body.json"
    if not doc_path.exists() or not body_path.exists():
        raise FileNotFoundError(
            f"no golden fork pair for {scenario_id} under {FIXTURES}; create it:\n"
            + REGENERATE.format(sid=scenario_id)
        )
    committed = json.loads(doc_path.read_text(encoding="utf-8"))
    live = corpus_doc_for_fixture(scenario_id)
    if committed != live:
        drift = sorted(k for k in set(committed) | set(live) if committed.get(k) != live.get(k))
        raise AssertionError(
            f"{doc_path.name} no longer matches the live corpus (keys {drift}). "
            "The scenario or the loader changed, so the committed fork body may "
            "not be what the console sends today. Regenerate both halves:\n"
            + REGENERATE.format(sid=scenario_id)
        )
    return json.loads(body_path.read_text(encoding="utf-8"))
