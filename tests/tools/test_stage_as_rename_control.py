"""`external_tools[].stage_as` — the authorable rename negative control.

This file began life as a TRIPWIRE. `payload_shelf.required_artifacts()` read
`external_tools[].stage_as`, `ExternalToolSchema` did not declare it, and a
plain Pydantic v2 model defaults to `extra="ignore"` — so a `stage_as:` key in a
scenario YAML was accepted without complaint, dropped before
`_schema_to_orm_kwargs`, and never reached `compose()`. A committed,
reviewable-looking declaration that did nothing, backing a claim ("we proved the
rename negative control") that was false.

The loader now carries the field, validated by **S-19** against the same staging
allowlist the composer enforces — a destination is a WRITE on a host the DC does
not own, and the beacon frequently runs as root.

WHY SIM-EDR-022 STILL DOES NOT DECLARE IT
-----------------------------------------
It is the **canonical arm** of the control, and the control needs two arms. Its
card declares four behavioural detections (service-account interpreter spawn,
read fan-out, discovery-binary breadth burst, SUID sweep) alongside two
explicitly labelled name-keyed targets — a `command_line contains "linpeas"`
BIOC and a `linpeas.sh` filename IOC. The canonical run must keep the real
filename so those two FIRE; the renamed run is what darkens them while the
behavioural four stay lit. Putting `stage_as` on the scenario itself would
collapse both arms into one and destroy the comparison. The rename arm is a
compose-request override (`POST /api/shelf/compose` with `overrides`), which is
exactly the per-run axis it should vary on.

So: this file now proves the machinery works and guards the properties a
renamed run depends on.
"""
from __future__ import annotations

import pytest
import yaml

from engine.payload_shelf import required_artifacts
from engine.scenario_loader import ExternalToolSchema


def test_the_loader_carries_stage_as():
    """The field is declared, typed and survives model_dump()."""
    tool = ExternalToolSchema(
        name="linpeas", type="script", adapter_ref="TOOL-LINPEAS",
        stage_as="/tmp/.cache/sysinfo.sh",
    )
    assert tool.stage_as == "/tmp/.cache/sysinfo.sh"
    assert tool.model_dump()["stage_as"] == "/tmp/.cache/sysinfo.sh"


def test_stage_as_defaults_to_none_so_every_existing_scenario_is_unchanged():
    tool = ExternalToolSchema(name="nmap", type="binary", adapter_ref="TOOL-NMAP")
    assert tool.stage_as is None


@pytest.mark.parametrize("bad", [
    "/usr/bin/sysinfo.sh",       # outside the staging allowlist
    "/etc/cron.d/sysinfo",       # a write that persists as root
    "relative/path.sh",          # not absolute
    "/tmp/../etc/passwd",        # traversal out of the allowlist
])
def test_s19_refuses_a_destination_outside_the_staging_allowlist(bad):
    """S-19 is the reason this field is safe to author.

    Without it a committed scenario would be an arbitrary-file-write primitive
    against a host the DC does not own — the beacon frequently runs as root, and
    a scenario is a file a customer may never read.
    """
    with pytest.raises(Exception) as exc:
        ExternalToolSchema(
            name="linpeas", type="script", adapter_ref="TOOL-LINPEAS", stage_as=bad,
        )
    assert "S-19" in str(exc.value) or "stage_as" in str(exc.value)


def test_the_resolver_honours_stage_as_from_a_loaded_scenario(repo_root):
    """End to end: the shelf KEY is unchanged, only the destination moves."""
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    catalog.load(str(repo_root / "tools" / "packs"))

    requests, unstaged = required_artifacts({
        "scenario_id": "SIM-EDR-022",
        "external_tools": [
            {"name": "linpeas", "type": "script", "adapter_ref": "TOOL-LINPEAS",
             "stage_as": "/tmp/.cache/sysinfo.sh"},
        ],
    })

    assert not unstaged
    assert len(requests) == 1
    req = requests[0]
    assert req.name == "linpeas.sh", "the SHELF KEY must never change under a rename"
    assert req.dest_path == "/tmp/.cache/sysinfo.sh"


def test_sim_edr_022_keeps_the_canonical_filename(repo_root):
    """The canonical arm must NOT rename — see this module's docstring.

    If someone adds `stage_as` here, the name-keyed detections the card declares
    as negative-control TARGETS can never fire, and the run that was supposed to
    prove they fire before the rename silently proves nothing.
    """
    doc = yaml.safe_load(
        (repo_root / "scenarios" / "edr"
         / "edr-022-shelf-staged-privesc-enumeration.yml").read_text()
    )
    renamed = [
        e for e in (doc.get("external_tools") or [])
        if isinstance(e, dict) and e.get("stage_as")
    ]
    assert not renamed, (
        "SIM-EDR-022 is the CANONICAL arm of the rename negative control and "
        "must keep the real filenames so its name-keyed detections fire. Vary "
        "the rename per-run via POST /api/shelf/compose overrides, or author a "
        "sibling scenario — not by renaming this one."
    )


def test_a_scenario_declaring_stage_as_now_actually_renames(repo_root):
    """Any corpus `stage_as` is honoured, so none of them is an inert claim.

    The inverse of the original tripwire: it asserted the corpus declared none
    because none would work. Now they work, so the property worth guarding is
    that every declared one resolves through the real resolver.
    """
    from tools.adapter_catalog import catalog  # noqa: PLC0415

    catalog.load(str(repo_root / "tools" / "packs"))

    for path in sorted((repo_root / "scenarios").rglob("*.yml")):
        if path.name.startswith("_"):
            continue
        try:
            doc = yaml.safe_load(path.read_text())
        except Exception:  # noqa: BLE001 - non-scenario YAML in the tree
            continue
        if not isinstance(doc, dict):
            continue
        declared = {
            e["adapter_ref"]: e["stage_as"]
            for e in (doc.get("external_tools") or [])
            if isinstance(e, dict) and e.get("stage_as") and e.get("adapter_ref")
        }
        if not declared:
            continue
        requests, _ = required_artifacts(doc)
        by_adapter = {r.adapter_id: r.dest_path for r in requests}
        for ref, dest in declared.items():
            assert by_adapter.get(ref) == dest, (
                f"{path.name} declares stage_as {dest!r} for {ref}, but the "
                f"resolver produced {by_adapter.get(ref)!r} — the file reads as "
                f"a proven rename control and the run does something else."
            )
