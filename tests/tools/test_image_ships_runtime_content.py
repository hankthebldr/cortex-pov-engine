"""Every content directory the app resolves at runtime must be in the image.

The defect this pins: `assertions/` was never COPYed into core/Dockerfile. The
app degrades politely when it is absent — AssertionCatalog loads 0 and returns
`rejected: []` — so a deployed SimCore served `GET /api/assertions` as an empty
list. 18 authored assertions on disk, 0 in the image, and the response for
"nothing was shipped" is byte-identical to the response for "nothing was
authored". Nothing failed. Nothing logged an error a DC would see.

`spec/identity_harness.json` was missing the same way, and cost nothing at all
today because `identity_spec._load()`'s hard-coded fallback happens to be
identical to the file — which is precisely why it survived. It makes the
"authoritative" spec decoration in every deployed image: edit the JSON and the
change takes effect in the test suite (BASE_DIR = repo root) and nowhere else.

Both are the same shape as the health probe that reported `{status: ok,
count: 0}` while booting without `tools/`, and as the agent-dist matrix that
shipped 4 targets while the build script emitted 5 (see
tests/installer/test_agent_dist_matrix_parity.py, the sibling of this file).
The pattern is always: content resolved from CORTEXSIM_BASE_DIR, a tolerant
loader, and a Dockerfile nobody re-read.

These tests are STATIC — they compare the resolvers against the Dockerfile
rather than build an image, because a build is minutes and this has to run on
every push. `make check-agent-shelf` is the built-image counterpart.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_TEXT = (REPO_ROOT / "core" / "Dockerfile").read_text()

# The runtime base dir inside the image. Set by `ENV CORTEXSIM_BASE_DIR=/app`.
IMAGE_BASE = "/app"


def _final_stage(text: str) -> str:
    """Only the last FROM matters — earlier stages are builders whose files
    reach the image solely via `COPY --from=`."""
    return text.split("\nFROM ")[-1]


def _copied_top_level_dirs() -> set[str]:
    """Top-level directories under /app that the final stage populates."""
    dirs: set[str] = set()
    for dest in re.findall(r"^COPY\s+(?:--from=\S+\s+)?\S+\s+(\S+)", _final_stage(DOCKERFILE_TEXT), re.M):
        if not dest.startswith(f"{IMAGE_BASE}/"):
            continue
        rel = dest[len(IMAGE_BASE) + 1:].strip("/")
        if rel:
            dirs.add(rel.split("/")[0])
    return dirs


def _resolved_top_level(path: str) -> str:
    """The top-level directory a resolver lands in, relative to the base dir."""
    rel = Path(path).resolve().relative_to(Path(IMAGE_BASE).resolve())
    return rel.parts[0]


def _runtime_content_paths() -> dict[str, str]:
    """Ask the REAL resolvers where they read from, with the image's base dir.

    Calling the production functions is the point: a test that restated the
    literals would keep passing after someone moved a resolver.
    """
    from engine.assertions import default_assertions_dir

    paths = {
        "assertions (POS/PLT/AUT corpus)": default_assertions_dir(IMAGE_BASE),
        "identity-harness spec": str(Path(IMAGE_BASE) / "spec" / "identity_harness.json"),
        "scenario corpus": str(Path(IMAGE_BASE) / "scenarios"),
        "TTP detection cards": str(Path(IMAGE_BASE) / "detection_scanner" / "ttps"),
        "tool-adapter packs": str(Path(IMAGE_BASE) / "tools" / "packs"),
        "UC/TC v2.2 snapshot": str(Path(IMAGE_BASE) / "docs" / "uc_tc_mapping" / "_v2.2-source"),
        "payload shelf": str(Path(IMAGE_BASE) / "payloads"),
        "IaC modules": str(Path(IMAGE_BASE) / "infra" / "modules"),
    }
    return paths


@pytest.mark.parametrize(
    ("label", "path"), sorted(_runtime_content_paths().items())
)
def test_every_runtime_content_dir_is_copied_into_the_image(label, path):
    top = _resolved_top_level(path)
    copied = _copied_top_level_dirs()
    assert top in copied, (
        f"core/Dockerfile never COPYs {top!r}, but the app resolves {label} "
        f"from {path} at runtime. The loaders here are deliberately tolerant, "
        f"so this does not crash — it serves an empty catalog that looks "
        f"exactly like 'nothing was authored'. Add a COPY to the final stage."
    )


def test_the_identity_spec_resolver_still_points_where_we_copy_it(monkeypatch):
    """Pin the resolver itself — the COPY above is only correct while it does."""
    import engine.identity_spec as id_spec
    from engine.identity_spec import _spec_path

    monkeypatch.setattr(id_spec.settings, "CORTEXSIM_BASE_DIR", IMAGE_BASE)
    assert _spec_path() == f"{IMAGE_BASE}/spec/identity_harness.json"


def test_assertions_dir_is_a_sibling_of_scenarios():
    """Guards the convention the COPY encodes, not just today's string."""
    from engine.assertions import default_assertions_dir

    assert default_assertions_dir(IMAGE_BASE) == f"{IMAGE_BASE}/assertions"


def test_the_authored_assertion_corpus_is_not_empty():
    """A COPY of an empty directory would satisfy the test above and still
    ship nothing. If this ever legitimately goes to zero, delete the COPY and
    this test together — do not let them drift apart quietly."""
    authored = list((REPO_ROOT / "assertions").rglob("*.yml"))
    assert authored, (
        "assertions/ holds no .yml — either the corpus was removed (drop the "
        "Dockerfile COPY and this test) or the checkout is incomplete."
    )
