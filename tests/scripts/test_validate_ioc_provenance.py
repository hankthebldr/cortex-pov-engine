"""Guard tests for validate.py::check_ioc_provenance and the IOC provenance schema fields.

Cards carrying real Unit 42 indicators must say where each one came from, and the
`source_url` must point at Unit 42 or the PaloAltoNetworks GitHub org -- not an
arbitrary host. These tests call the check directly with a recording fake report.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATE = REPO_ROOT / "detection_scanner" / "scripts" / "validate.py"
SCHEMA = REPO_ROOT / "detection_scanner" / "schema" / "ttp-entry.schema.json"

_spec = importlib.util.spec_from_file_location("ttp_validate", VALIDATE)
validate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validate)

UNIT42_REFS = [{"publisher_id": "SRC-UNIT42", "primary": True}]
OTHER_REFS = [{"publisher_id": "SRC-MITRE", "primary": True}]


class FakeReport:
    def __init__(self):
        self.errs = []
        self.warns = []

    def err(self, where, msg):
        self.errs.append((where, msg))

    def warn(self, where, msg):
        self.warns.append((where, msg))


def _run(iocs, refs, ttp_id="TTP-2026-0190"):
    r = FakeReport()
    validate.check_ioc_provenance(r, "card.json", {"iocs": iocs}, refs, ttp_id=ttp_id)
    return r


def test_bad_source_url_host_is_an_error():
    r = _run([{"ioc_type": "domain", "value": "evil.example", "source_url": "https://example.com/post"}],
             UNIT42_REFS)
    assert len(r.errs) == 1 and not r.warns


def test_unit42_host_requires_https():
    r = _run([{"ioc_type": "domain", "value": "evil.example",
               "source_url": "http://unit42.paloaltonetworks.com/post"}], UNIT42_REFS)
    assert len(r.errs) == 1 and not r.warns


def test_unit42_host_https_passes():
    r = _run([{"ioc_type": "domain", "value": "evil.example",
               "source_url": "https://unit42.paloaltonetworks.com/post"}], UNIT42_REFS)
    assert not r.errs


def test_unit42_card_with_bare_ipv4_warns():
    r = _run([{"ioc_type": "ipv4", "value": "203.0.113.7"}], UNIT42_REFS)
    assert len(r.warns) == 1 and not r.errs


def test_non_unit42_card_with_bare_ipv4_is_silent():
    r = _run([{"ioc_type": "ipv4", "value": "203.0.113.7"}], OTHER_REFS)
    assert not r.warns and not r.errs


def test_valid_provenance_is_silent():
    r = _run([{"ioc_type": "domain", "value": "evil.example",
               "source_url": "https://unit42.paloaltonetworks.com/some-post/",
               "published": "2025-04", "safe_form": "dns_lookup"}], UNIT42_REFS)
    assert not r.warns and not r.errs


def test_github_paloaltonetworks_prefix_accepted():
    r = _run([{"ioc_type": "url", "value": "http://x.example/a",
               "source_url": "https://github.com/PaloAltoNetworks/Unit42-timely-threat-intel/blob/main/x.txt"}],
             UNIT42_REFS)
    assert not r.warns and not r.errs


def test_invalid_safe_form_and_published_are_errors():
    r = _run([{"ioc_type": "domain", "value": "a.example", "safe_form": "live_c2", "published": "April 2025"}],
             OTHER_REFS)
    assert len(r.errs) == 2


def test_pre_0182_unit42_card_does_not_warn():
    r = _run([{"ioc_type": "ipv4", "value": "203.0.113.7"}], UNIT42_REFS, ttp_id="TTP-2026-0100")
    assert not r.warns and not r.errs


def test_schema_accepts_source_url_rejects_sauce_url():
    schema = json.loads(SCHEMA.read_text())
    ioc_schema = schema["properties"]["detections"]["properties"]["iocs"]["items"]
    v = Draft202012Validator(ioc_schema)
    base = {"ioc_type": "domain", "value": "evil.example"}
    assert not list(v.iter_errors({**base, "source_url": "https://unit42.paloaltonetworks.com/x/"}))
    assert list(v.iter_errors({**base, "sauce_url": "https://unit42.paloaltonetworks.com/x/"}))


# ── Final review M5: the GitHub org prefix compares case-insensitively ──────────
def test_github_prefix_is_case_insensitive():
    for url in ("https://github.com/paloaltonetworks/Unit42-timely-threat-intel/x.txt",
                "https://GitHub.com/PALOALTONETWORKS/Unit42-timely-threat-intel/x.txt",
                "https://github.com/PaloAltoNetworks/Unit42-timely-threat-intel/x.txt"):
        r = _run([{"ioc_type": "domain", "value": "evil.example", "source_url": url}], UNIT42_REFS)
        assert not r.errs, url


def test_github_prefix_still_rejects_other_orgs():
    r = _run([{"ioc_type": "domain", "value": "evil.example",
               "source_url": "https://github.com/paloaltonetworks-fake/x.txt"}], UNIT42_REFS)
    assert len(r.errs) == 1
