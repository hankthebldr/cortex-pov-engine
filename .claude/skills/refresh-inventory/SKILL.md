---
name: refresh-inventory
description: Recompute CortexSim's canonical ground-truth counts from disk (loadable scenarios · detection planes · TTP cards · resolvable detection objects · detection_id slug resolution · EAL plugins · tool-adapter packs · IaC modules) and update the two places that carry them — CLAUDE.md's "Canonical scenario count" paragraph and docs/reference/README.md's "Counted ground truth" block. Use after adding/removing scenarios, cards, planes, plugins, or adapters, or when the user asks to "refresh the counts", "update the inventory", or "recount the corpus". Reconciles the docs to reality; flags drift.
disable-model-invocation: true
---

# refresh-inventory

The repo advertises hand-maintained ground-truth counts, and every content change
drifts them. This skill recomputes them from disk and reconciles the two documents
that quote them, so the numbers a reader trusts stay true.

> **The docs are derived, the code is authoritative.** `docs/reference/README.md`
> says it plainly: when a doc and the code disagree, the code wins. The *final*
> authority on "loadable scenarios / 0 rejected / N/N slugs resolve" is the
> **boot-time loader inside the prod image** (it needs Python 3.11 + SQLAlchemy,
> unavailable on the host). Host-side one-liners below are the fast path; confirm
> the headline counts against a real boot before publishing a new "verified" date.

## The two documents to update

1. **`CLAUDE.md`** — the `> **Canonical scenario count.**` paragraph (one blockquote).
2. **`docs/reference/README.md`** — the `**Counted ground truth (verified <date>)**`
   bullet block. Add a new dated bullet; keep the prior one as history (that file
   already keeps a dated trail — match its style, don't overwrite the lineage).

## Recompute from disk

Run these and record each number. They mirror the loader's skip rules; the loader
globs `ttps/*.json` (skipping `_drafts/`) and `scenarios/**/*.yml` minus
`_schema.yml` and the non-scenario sub-trees (`probes/`, `packages/`, `campaigns/`).

```bash
# --- TTP cards (active corpus; engine skips _drafts/) ---
ls detection_scanner/ttps/*.json | wc -l

# --- Resolvable detection objects across all cards ---
python3 - <<'PY'
import glob, json
n = 0
for f in glob.glob("detection_scanner/ttps/*.json"):
    d = json.load(open(f)).get("detections", {})
    for k in ("biocs","xql_queries","abiocs","iocs","correlation_rules","modeling_rules"):
        n += len(d.get(k, []) or [])
print("detection objects:", n)
PY

# --- Loadable scenarios (mirror the loader's skip rules) ---
python3 - <<'PY'
from pathlib import Path
SKIP = {"probes","packages","campaigns"}
files = [p for p in Path("scenarios").rglob("*.yml")
         if p.name != "_schema.yml" and SKIP.isdisjoint(p.parts)]
print("loadable scenarios:", len(files))
planes = sorted({p.parts[1] for p in files if len(p.parts) > 2})
print("plane dirs:", len(planes), planes)
PY

# --- EAL plugins · tool-adapter packs · AWS IaC modules ---
# (find, not a glob — a bare *.yaml with no match aborts the line under zsh)
find core/eal_simulator/plugins -maxdepth 1 -name '*.py' ! -name '__init__.py' | wc -l  # EAL plugins
find tools/packs -maxdepth 1 \( -name '*.yml' -o -name '*.yaml' \) | wc -l               # adapter packs
find infra/modules/aws -maxdepth 1 -mindepth 1 -type d | wc -l                           # AWS IaC modules
```

For the two claims the host can't fully prove — **0 rejected** and **N/N
`detection_id` slugs resolve** — run the ground-truth validators, and for the
headline, a real boot:

```bash
python3 detection_scanner/scripts/validate.py            # TTP-card corpus: 0 fail
make validate                                            # CI detection + adapter gates
# Final authority for scenario load + slug resolution (Python 3.11 image):
make test-backend    # or: docker compose logs simcore | grep -iE "loaded|rejected|slug"
```

`adapter_ref` wiring counts (e.g. "34 distinct adapters wired across 35 scenarios")
come from the scenarios, not the packs directory:

```bash
grep -rhoE 'adapter_ref:\s*TOOL-[A-Z0-9_-]+' scenarios/ | sort -u | wc -l   # distinct wired
grep -rlE 'adapter_ref:\s*TOOL-' scenarios/ | wc -l                         # scenarios wiring ≥1
```

## Reconcile and report

1. **Diff computed vs documented.** For each number, compare against what CLAUDE.md
   and README.md currently claim. Build a small table: metric · documented · computed
   · Δ.
2. **If nothing drifted**, say so — don't churn the docs for a no-op. Just confirm
   the numbers still hold and name the ones you checked.
3. **If it drifted**, update **both** documents: the CLAUDE.md paragraph in place, and
   a new dated `**Counted ground truth (verified <date>)**` bullet in README.md above
   the prior one (preserve history). Keep the surrounding prose — only the numbers and
   the "what's new since" clause change. Get today's date from the session context;
   don't invent one.
4. **Never touch `manifest.json`** — gitignored, not an engine input (RUNBOOK).
5. **Offer to commit** the doc updates on one boundary (named-file `git add`, no
   `-A`), message in the repo's conventional style (e.g.
   `docs(reference): refresh canonical counts — N scenarios / M cards`).

Report the metric table, which docs you changed (or that none needed changing), and
whether the headline was confirmed against a real boot or only the host fast-path.
