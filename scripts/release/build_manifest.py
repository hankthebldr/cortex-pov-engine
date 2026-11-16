#!/usr/bin/env python3
"""Generate manifest.json for a release.

Moved out of release.yml's "Generate manifest.json" step so it can be tested.
Run from the release directory (dist/) with the env vars TAG, IMAGE, DIGEST,
REPO and AGENT_GO_VERSION set; SHELF_SOURCES names payloads/sources.json.
"""
import datetime
import hashlib
import json
import os
import pathlib

SHELF_FIELDS = ("name", "sha256", "license", "pin_type", "pin_ref", "adapter_id")


def build_manifest(
    dist: pathlib.Path,
    *,
    tag: str,
    image: str,
    digest: str,
    repo: str,
    agent_go_version: str,
    shelf_sources: pathlib.Path,
    installer: dict | None = None,
) -> dict:
    d = pathlib.Path(dist)
    artifacts = []
    for p in sorted(d.iterdir()):
        if p.name in ("SHA256SUMS", "manifest.json") or p.is_dir():
            continue
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        artifacts.append({
            "name": p.name,
            "size": p.stat().st_size,
            "sha256": h,
            "url": f"https://github.com/{repo}/releases/download/{tag}/{p.name}",
        })
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    manifest = {
        "schema_version": 1,
        "release": tag,
        "generated_at": now.isoformat(timespec="seconds") + "Z",
        "image": {
            "ref": f"{image}:{tag}",
            "latest": f"{image}:latest",
            "digest": digest,
            "platforms": ["linux/amd64", "linux/arm64"],
        },
        "artifacts": artifacts,
    }
    # The beacon matrix, described rather than just listed, so a reader
    # of the manifest alone learns what the binaries are NOT.
    extras = {"cortexsim-agent-SHA256SUMS", "cortexsim-agent-buildinfo.txt"}
    beacons = []
    for p in sorted(d.glob("cortexsim-agent-*")):
        if p.name in extras:
            continue
        goos, goarch = p.name[len("cortexsim-agent-"):].removesuffix(".exe").split("-", 1)
        beacons.append({
            "name": p.name,
            "os": goos,
            "arch": goarch,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        })
    if len(beacons) != 5 or not all((d / x).is_file() for x in extras):
        raise SystemExit(f"beacon assets incomplete: {[b['name'] for b in beacons]}")
    manifest["agent"] = {
        "go_version": agent_go_version,
        "build": "scripts/build-agent-dist.sh: CGO_ENABLED=0 go build -trimpath -ldflags='-s -w'",
        "signed": False,
        "executed_on_target_os_by_release": False,
        "sha256sums": "cortexsim-agent-SHA256SUMS",
        "buildinfo": "cortexsim-agent-buildinfo.txt",
        "binaries": beacons,
        "notes": [
            "Not code-signed: no Authenticode signature on the Windows .exe, no Apple notarization on the darwin builds.",
            "Built and structurally checked (file set, PE header, embedded GOOS) on Linux by this run; not executed on Windows or macOS by this run.",
            "Not expected to be byte-identical to the beacon baked into the container image, which core/Dockerfile builds with its own Go toolchain. Verify each copy against its own checksums, never across the two.",
        ],
    }
    # The payload shelf the image (and so every package) carries, one pinned
    # entry per staged artifact, taken from the generated payloads/sources.json.
    sources = json.loads(pathlib.Path(shelf_sources).read_text())
    manifest["shelf"] = [
        {k: p[k] for k in SHELF_FIELDS} for p in sources["payloads"]
    ]
    if installer is not None:
        manifest["installer"] = installer
    return manifest


def main() -> None:
    manifest = build_manifest(
        pathlib.Path("."),
        tag=os.environ["TAG"],
        image=os.environ["IMAGE"],
        digest=os.environ.get("DIGEST", ""),
        repo=os.environ["REPO"],
        agent_go_version=os.environ["AGENT_GO_VERSION"],
        shelf_sources=pathlib.Path(os.environ["SHELF_SOURCES"]),
    )
    out = pathlib.Path("manifest.json")
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(out.read_text())


if __name__ == "__main__":
    main()
