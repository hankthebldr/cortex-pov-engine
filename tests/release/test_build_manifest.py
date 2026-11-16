"""The release manifest generator (scripts/release/build_manifest.py)."""
import hashlib
import importlib.util
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "release" / "build_manifest.py"
REAL_SOURCES = REPO / "payloads" / "sources.json"

BEACONS = [
    "cortexsim-agent-linux-amd64",
    "cortexsim-agent-linux-arm64",
    "cortexsim-agent-darwin-amd64",
    "cortexsim-agent-darwin-arm64",
    "cortexsim-agent-windows-amd64.exe",
]
KW = dict(
    tag="v9.9.9",
    image="ghcr.io/acme/cortexsim",
    digest="sha256:abc",
    repo="acme/cortex-pov-engine",
    agent_go_version="go1.21.13",
)


def _load():
    spec = importlib.util.spec_from_file_location("build_manifest", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dist(tmp_path, beacons=BEACONS):
    d = tmp_path / "dist"
    d.mkdir()
    for b in beacons:
        (d / b).write_bytes(b"bin-" + b.encode())
    (d / "cortexsim-agent-SHA256SUMS").write_text("sums\n")
    (d / "cortexsim-agent-buildinfo.txt").write_text("info\n")
    (d / "SHA256SUMS").write_text("ignored\n")
    (d / "subdir").mkdir()
    return d


def test_manifest_matches_the_inline_generator(tmp_path):
    d = _dist(tmp_path)
    m = _load().build_manifest(d, shelf_sources=REAL_SOURCES, **KW)

    assert m["schema_version"] == 1
    assert m["release"] == "v9.9.9"
    assert m["generated_at"].endswith("Z") and len(m["generated_at"]) == 20
    assert m["image"] == {
        "ref": "ghcr.io/acme/cortexsim:v9.9.9",
        "latest": "ghcr.io/acme/cortexsim:latest",
        "digest": "sha256:abc",
        "platforms": ["linux/amd64", "linux/arm64"],
    }
    names = [a["name"] for a in m["artifacts"]]
    assert names == sorted(names)
    assert "SHA256SUMS" not in names and "manifest.json" not in names
    assert "subdir" not in names
    a = next(x for x in m["artifacts"] if x["name"] == "cortexsim-agent-linux-amd64")
    assert a["size"] == len(b"bin-cortexsim-agent-linux-amd64")
    assert a["sha256"] == hashlib.sha256(b"bin-cortexsim-agent-linux-amd64").hexdigest()
    assert a["url"] == (
        "https://github.com/acme/cortex-pov-engine/releases/download/v9.9.9/"
        "cortexsim-agent-linux-amd64"
    )

    agent = m["agent"]
    assert agent["go_version"] == "go1.21.13"
    assert agent["build"] == (
        "scripts/build-agent-dist.sh: CGO_ENABLED=0 go build -trimpath -ldflags='-s -w'"
    )
    assert agent["signed"] is False
    assert agent["executed_on_target_os_by_release"] is False
    assert agent["sha256sums"] == "cortexsim-agent-SHA256SUMS"
    assert agent["buildinfo"] == "cortexsim-agent-buildinfo.txt"
    assert [b["name"] for b in agent["binaries"]] == sorted(BEACONS)
    win = next(b for b in agent["binaries"] if b["name"].endswith(".exe"))
    assert (win["os"], win["arch"]) == ("windows", "amd64")
    assert len(agent["notes"]) == 3
    assert agent["notes"][0].startswith("Not code-signed")
    assert "installer" not in m


def test_manifest_lists_shelf_artifacts(tmp_path):
    d = _dist(tmp_path)
    m = _load().build_manifest(d, shelf_sources=REAL_SOURCES, **KW)
    real = json.loads(REAL_SOURCES.read_text())["payloads"]
    assert len(m["shelf"]) == len(real) == 8
    for entry, src in zip(m["shelf"], real):
        assert set(entry) == {
            "name", "sha256", "license", "pin_type", "pin_ref", "adapter_id",
        }
        assert entry == {k: src[k] for k in entry}


def test_manifest_passes_installer_block_through(tmp_path):
    d = _dist(tmp_path)
    m = _load().build_manifest(
        d, shelf_sources=REAL_SOURCES, installer={"packages": []}, **KW
    )
    assert m["installer"] == {"packages": []}


def test_manifest_refuses_incomplete_beacons(tmp_path):
    d = _dist(tmp_path, BEACONS[:4])
    with pytest.raises(SystemExit):
        _load().build_manifest(d, shelf_sources=REAL_SOURCES, **KW)
