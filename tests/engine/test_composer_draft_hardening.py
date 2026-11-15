"""SEC-05 / SEC-13 — what a Composer draft may persist.

Two hostile inputs the draft schema accepted, both from an unauthenticated
``POST /api/scenarios/drafts``:

* **SEC-05** — ``verification_xql`` carrying XQL's ``target`` stage. A draft
  is launchable and its results are verified against the tenant, so a saved
  ``| target type=lookup append=false <name>`` is a deferred write to the
  customer's Cortex. The client refuses it at send time too; refusing at save
  means the bad query is never stored, never shown as "the verification query"
  in a report, and never retried by the sweep.
* **SEC-13 (schema half)** — CR/LF and other control characters in fields that
  are single-line by meaning: the draft ``name`` and ``author``, ``tags``, step
  ``id``/``name``, and detection ``description``. The push generator echoes
  those into ``#`` comments and ``echo``/``Write-Host`` lines of bash and
  PowerShell bundles; a newline there ends the comment and the next line runs
  as a command on the customer endpoint. The generator half (escaping at emit
  time) lives in the push-bundle lane; this half stops the value at the door.
  ``command`` is multi-line by design and is NOT restricted here.

RED on base cc26ddf: every case below saved.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError


def _draft(**over):
    body = {
        "name": "Hardening probe",
        "plane": "EDR",
        "steps": [{
            "id": "step-01",
            "name": "Enumerate",
            "command": "id\nwhoami",
            "expected_detections": [{
                "plane": "EDR", "type": "XQL",
                "description": "process lineage visible",
                "verification_xql": "dataset = xdr_data | filter x = 1 | limit 1",
            }],
        }],
    }
    body.update(over)
    return body


def _validate(body):
    from engine.composer_draft_schema import DraftScenarioSchema

    return DraftScenarioSchema.model_validate(body)


def test_baseline_draft_is_valid():
    d = _validate(_draft())
    assert d.steps[0].command == "id\nwhoami", "multi-line commands stay legal"


def test_target_stage_in_verification_xql_is_refused():
    body = _draft()
    body["steps"][0]["expected_detections"][0]["verification_xql"] = (
        "dataset = xdr_data | target type=lookup append=false x")
    with pytest.raises(ValidationError) as exc:
        _validate(body)
    msg = str(exc.value)
    assert "target" in msg and "step-01" in msg, msg


def test_target_inside_a_string_literal_is_allowed():
    body = _draft()
    body["steps"][0]["expected_detections"][0]["verification_xql"] = (
        'dataset = xdr_data | filter action_process_image_name = "target.exe"')
    _validate(body)


BAD = ["two\nlines", "carriage\rreturn", "tab\tbed", "nul\x00byte",
       "esc\x1b[31m", "del\x7f", "nel\x85", "ls sep", "ps sep"]


@pytest.mark.parametrize("value", BAD)
@pytest.mark.parametrize("where", ["name", "author", "tag", "step_id", "step_name",
                                   "detection_description"])
def test_control_characters_in_single_line_fields_are_refused(where, value):
    body = copy.deepcopy(_draft())
    step = body["steps"][0]
    if where == "name":
        body["name"] = value
    elif where == "author":
        body["author"] = value
    elif where == "tag":
        body["tags"] = ["ok", value]
    elif where == "step_id":
        step["id"] = value
    elif where == "step_name":
        step["name"] = value
    elif where == "detection_description":
        step["expected_detections"][0]["description"] = value

    with pytest.raises(ValidationError) as exc:
        _validate(body)
    msg = str(exc.value)
    assert "control character" in msg, msg


def test_ordinary_unicode_is_fine():
    _validate(_draft(name="Détection – ünïcode “quotes” 日本語", author="Zoë"))
