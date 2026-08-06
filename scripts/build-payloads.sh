#!/usr/bin/env bash
# ─── Stage the tool artifacts the K8s delivery serves ────────────────────────
#
# WHY: in the K8s delivery mode THE POD fetches its tools. If it fetched them
# from github.com we would have moved the customer's egress problem, not solved
# it — the clusters that buy Cortex Cloud are exactly the ones running
# default-deny egress. Staging here moves the public-internet dependency onto
# the DC's OWN host, where it is already accepted, and makes every artifact
# checksummable before it ever reaches a customer's cluster.
#
# Deliberately NOT a Dockerfile stage that downloads at build time: that would
# make `docker build` need network, which breaks the offline/air-gapped build
# this feature exists to serve. Run this first; core/Dockerfile then COPYs the
# shelf into the image.
#
#   ./scripts/build-payloads.sh            # stage everything in payloads/sources.json
#   OUT_DIR=/srv/shelf ./scripts/build-payloads.sh
#   PAYLOAD_OFFLINE=1 ./scripts/build-payloads.sh    # no network: empty shelf, exit 0
#   PAYLOAD_ALLOW_UNPINNED=0 ./scripts/build-payloads.sh   # CI: refuse unpinned
#
# SimCore serves the result from GET /api/shelf/payload/<name>; an empty shelf is
# a valid state (the generator then reports every declared tool as unstaged).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$REPO_ROOT/payloads}"
SOURCES="${SOURCES:-$REPO_ROOT/payloads/sources.json}"
PAYLOAD_OFFLINE="${PAYLOAD_OFFLINE:-0}"
PAYLOAD_ALLOW_UNPINNED="${PAYLOAD_ALLOW_UNPINNED:-1}"

mkdir -p "$OUT_DIR"

# sha256sum (GNU) or shasum -a 256 (macOS) — same fallback build-agent-dist.sh uses.
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else echo "[payloads] FATAL no sha256sum or shasum — cannot verify anything" >&2; exit 1
  fi
}

write_manifest() {  # write_manifest <json-array-body>
  cat > "$OUT_DIR/MANIFEST.json" <<EOF
{
  "generated_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "offline": $([ "$PAYLOAD_OFFLINE" = "1" ] && echo true || echo false),
  "payloads": [$1]
}
EOF
}

if [ "$PAYLOAD_OFFLINE" = "1" ]; then
  # An image with an empty shelf boots fine. The honest state is then surfaced
  # by the generator, not hidden: every declared tool reports as unstaged and
  # GET /api/k8s/bootstrap/{id} refuses with PAYLOAD_NOT_STAGED rather than
  # letting a pod run the scenario without its tooling.
  echo "[payloads] PAYLOAD_OFFLINE=1 — skipping all downloads, writing an empty shelf manifest"
  write_manifest ""
  echo "[payloads] done — shelf is EMPTY at $OUT_DIR"
  exit 0
fi

if [ ! -f "$SOURCES" ]; then
  echo "[payloads] FATAL no source declaration at $SOURCES" >&2
  exit 1
fi

# Parse the declaration with python3 (present wherever this repo builds) rather
# than with sed: a JSON parse that silently yields "" is how a staging step
# quietly becomes a no-op.
#
# Empty fields are emitted as "-", NOT as an empty string: bash collapses runs
# of an IFS whitespace character, so a genuinely empty sha256 between two tabs
# would shift `license` into `PIN` and the build would compare the digest
# against "MIT". Found by running it.
#
# TWO LISTS, one derived and one shrinking:
#   payloads[] — GENERATED from tools/packs/*.yml install.artifact blocks by
#                `python3 -m engine.payload_shelf --write`. A CI gate fails on
#                drift, so this is the authoritative declaration.
#   unbound[]  — the migration bucket: an artifact that is stageable but has no
#                adapter pack yet. Staged identically, but NOTHING can compose
#                it (no adapter_ref resolves to it), so it must reach zero.
# Both are staged here because a shelf that silently skipped the unbound half
# would make `PAYLOAD_OFFLINE=0` produce a partially-filled shelf that reads as
# a full one.
mapfile -t ROWS < <(python3 - "$SOURCES" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
for p in list(doc.get("payloads", [])) + list(doc.get("unbound", [])):
    print("\t".join([
        str(p.get(k) or "-") for k in ("name", "url", "sha256", "license", "kind")
    ]))
PY
)

ENTRIES=""
STAGED=0
UNPINNED=0

for row in "${ROWS[@]}"; do
  [ -n "$row" ] || continue
  IFS=$'\t' read -r NAME URL PIN LICENSE KIND <<<"$row"
  [ "$PIN" = "-" ] && PIN=""
  [ "$LICENSE" = "-" ] && LICENSE=""
  [ "$KIND" = "-" ] && KIND="file"
  if [ "$NAME" = "-" ] || [ "$URL" = "-" ] || [ -z "$NAME" ] || [ -z "$URL" ]; then
    echo "[payloads] FATAL malformed entry in $SOURCES (name/url required)" >&2
    exit 1
  fi
  # `file` is the only kind any consumer can handle. Re-verified 2026-08-06:
  # agent/beacon/artifact.go's Artifact struct has NO `Kind` field at all and
  # nothing under agent/ imports archive/tar|archive/zip|compress/gzip, and the
  # K8s init container is a wget->sha256sum->chmod->mv loop on an image with no
  # `unzip`. Staging an archive nothing can unpack would put a tool on the shelf
  # that silently never lands — worse than a pack that honestly still uses
  # runtime_install_command.
  if [ "$KIND" != "file" ]; then
    echo "[payloads] FATAL $NAME declares kind=$KIND; only 'file' is supported" >&2
    echo "[payloads]   No consumer can unpack an archive (see docs/reference/payload-shelf.md §11)." >&2
    echo "[payloads]   A pack whose upstream ships only an archive declares" >&2
    echo "[payloads]   install.artifact_exempt with reason_code ARCHIVE_ONLY_NO_EXTRACTOR." >&2
    exit 1
  fi

  echo "[payloads] fetching $NAME <- $URL"
  if ! curl -fsSL --retry 3 --max-time 300 -o "$OUT_DIR/$NAME.part" "$URL"; then
    rm -f "$OUT_DIR/$NAME.part"
    echo "[payloads] FATAL download failed for $NAME ($URL)" >&2
    echo "[payloads]   a half-staged shelf is worse than an empty one — refusing to continue" >&2
    exit 1
  fi

  GOT="$(sha256 "$OUT_DIR/$NAME.part")"
  PINNED=false
  if [ -n "$PIN" ]; then
    if [ "$PIN" != "$GOT" ]; then
      rm -f "$OUT_DIR/$NAME.part"
      echo "[payloads] PAYLOAD_PIN_MISMATCH name=$NAME expected=$PIN got=$GOT" >&2
      echo "[payloads]   upstream changed. A build must never bless that silently." >&2
      exit 1
    fi
    PINNED=true
  else
    UNPINNED=$((UNPINNED+1))
    echo "[payloads]   computed $GOT for $NAME — pin it: set \"sha256\" in $SOURCES"
    if [ "$PAYLOAD_ALLOW_UNPINNED" = "0" ]; then
      rm -f "$OUT_DIR/$NAME.part"
      echo "[payloads] FATAL PAYLOAD_ALLOW_UNPINNED=0 and $NAME has no pin" >&2
      exit 1
    fi
  fi

  mv "$OUT_DIR/$NAME.part" "$OUT_DIR/$NAME"
  chmod 0644 "$OUT_DIR/$NAME"
  SIZE="$(wc -c < "$OUT_DIR/$NAME" | tr -d ' ')"
  STAGED=$((STAGED+1))
  [ -n "$ENTRIES" ] && ENTRIES="$ENTRIES,"
  ENTRIES="$ENTRIES
    {\"name\": \"$NAME\", \"source_url\": \"$URL\", \"sha256\": \"$GOT\", \"size_bytes\": $SIZE, \"license\": \"$LICENSE\", \"pinned\": $PINNED}"
done

write_manifest "$ENTRIES"

# For humans and `sha256sum -c`. The ENDPOINTS always recompute from the bytes
# on disk, so a stale SHA256SUMS can never make a tampered file verify.
( cd "$OUT_DIR" && \
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum $(python3 -c 'import json,sys;print(" ".join(p["name"] for p in json.load(open("MANIFEST.json"))["payloads"]))') > SHA256SUMS 2>/dev/null || true
  else
    shasum -a 256 $(python3 -c 'import json,sys;print(" ".join(p["name"] for p in json.load(open("MANIFEST.json"))["payloads"]))') > SHA256SUMS 2>/dev/null || true
  fi )

echo "[payloads] staged $STAGED artifact(s) into $OUT_DIR ($UNPINNED unpinned)"
echo "[payloads] done — SimCore serves these from GET /api/shelf/payload/<name>"
