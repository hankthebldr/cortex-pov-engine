"""Every third-party action is pinned to a full commit SHA (CI-12).

``uses: actions/checkout@v4`` runs whatever commit the tag ``v4`` points at on
the day the workflow runs, and a tag is a pointer its owner (or whoever
compromises its owner) can move. The release workflow holds ``packages: write``
and ``contents: write``: it publishes the image and the beacons a DC installs
in a customer lab. Round 1 cut those scopes down per job; the actions were
still floating.

Each ``uses:`` is now ``owner/repo@<40 hex> # <tag>``. The SHA is what runs; the
comment is what a reviewer and Dependabot read. ``.github/dependabot.yml``
proposes the new SHA when an action releases, so a pin is a decision that gets
revisited rather than one that rots.

These tests refuse a floating ref. They do not, and cannot offline, prove that a
given SHA is the commit its comment names: that was resolved once against the
action repositories when the pins were written, and Dependabot re-resolves it on
every bump.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.ci._wiring import load_workflow, workflow_files

_USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)\s*(?:#\s*(?P<tag>\S+))?\s*$")
_PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
_TAG = re.compile(r"^v\d+(?:\.\d+){0,2}$")


def _uses_from_yaml(doc: dict) -> list[str]:
    out = []
    for job in (doc.get("jobs") or {}).values():
        if "uses" in job:  # a reusable workflow
            out.append(str(job["uses"]))
        for step in job.get("steps", []):
            if "uses" in step:
                out.append(str(step["uses"]))
    return out


def _uses_lines(path: Path) -> list[tuple[int, str, str | None]]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        m = _USES_LINE.match(line)
        if m:
            rows.append((n, m["ref"], m["tag"]))
    return rows


def test_every_third_party_action_is_pinned_to_a_full_commit_sha():
    total = 0
    problems = []
    for path in workflow_files():
        lines = _uses_lines(path)
        # The line scan must see every `uses:` the YAML has, or a form it does
        # not recognise would slip through unexamined.
        assert sorted(ref for _, ref, _ in lines) == sorted(_uses_from_yaml(load_workflow(path))), path.name
        for n, ref, tag in lines:
            if ref.startswith("./"):
                continue  # this repository's own action or workflow
            total += 1
            if not _PINNED.match(ref):
                problems.append(f"{path.name}:{n}: {ref} is not pinned to a 40-hex commit SHA")
            elif not tag or not _TAG.match(tag):
                problems.append(f"{path.name}:{n}: {ref} has no trailing `# <tag>` comment")
    assert total > 0, "found no third-party action at all -- the scan is broken"
    assert problems == [], "\n".join(problems)


def test_one_action_tag_resolves_to_one_sha_across_all_workflows():
    """Two workflows pinning `actions/checkout # v4` to different commits means
    one of them was bumped by hand and the other forgotten."""
    seen: dict[tuple[str, str], set[str]] = {}
    for path in workflow_files():
        for _, ref, tag in _uses_lines(path):
            if ref.startswith("./") or "@" not in ref:
                continue
            action, sha = ref.split("@", 1)
            seen.setdefault((action, tag or ""), set()).add(sha)
    assert seen, "found no action reference at all -- the scan is broken"
    drift = {f"{a} # {t}": sorted(s) for (a, t), s in seen.items() if len(s) > 1}
    assert drift == {}, f"the same action tag is pinned to different commits: {drift}"


def test_the_scan_rejects_the_forms_it_exists_to_reject(tmp_path):
    """The guard, shown going red on each floating form."""
    cases = {
        "      - uses: actions/checkout@v4": "not pinned",
        "      - uses: actions/checkout@main": "not pinned",
        "        uses: docker/build-push-action@v5.4.0": "not pinned",
        "      - uses: actions/checkout@abc1234": "not pinned",  # an abbreviated SHA is still ambiguous
        "      - uses: actions/checkout@" + "a" * 40: "no trailing",
        "      - uses: actions/checkout@" + "a" * 40 + " # latest": "no trailing",
    }
    for line, why in cases.items():
        f = tmp_path / "wf.yml"
        f.write_text(line + "\n", encoding="utf-8")
        ((_, ref, tag),) = _uses_lines(f)
        if why == "not pinned":
            assert not _PINNED.match(ref), line
        else:
            assert _PINNED.match(ref) and not (tag and _TAG.match(tag)), line
    good = tmp_path / "ok.yml"
    good.write_text("      - uses: actions/checkout@" + "a" * 40 + " # v4\n", encoding="utf-8")
    ((_, ref, tag),) = _uses_lines(good)
    assert _PINNED.match(ref) and _TAG.match(tag)
