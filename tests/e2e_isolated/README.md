# tests/e2e_isolated

Tiered e2e suite that confirms scenario TTP scripts execute correctly —
not just that the UI navigates and the API returns 200.

See `docs/design/e2e-execution-methodology.md` for the full methodology.

## Tiers

| Tier | File | What it checks |
|------|------|----------------|
| A | `test_tier_a_static.py` | shellcheck + `bash -n` on every hand-written TTP script and every `run.sh` |
| B | `test_tier_b_push_bundle.py` | Same checks on the GENERATED push-mode bundles for every scenario YAML + step-presence, harness presence, cleanup presence, placeholder-leak guards |
| C | `test_tier_c_isolated_exec.py` (pending — phase 4) | Real execution in isolated container with auditd + sinkhole |

## Run locally

```bash
# Tier A only (fast, ~1s)
pytest tests/e2e_isolated/test_tier_a_static.py -v

# Full e2e isolated suite (once tiers B+C ship)
pytest tests/e2e_isolated -v
```

Requires `shellcheck` on PATH (`brew install shellcheck` /
`apt-get install shellcheck`).

## Adding new TTP scripts

Every `*.sh` under `scenarios/*/packages/*/ttps/` and every
`scenarios/*/packages/*/run.sh` is automatically discovered and
checked. No registration needed — just drop the script in.

If shellcheck flags an intentional pattern, add a per-line directive:

```bash
# shellcheck disable=SC2086  # word splitting intentional here
something $with $unquoted $args
```

Avoid blanket file-level disables — be specific about which code and
why.
