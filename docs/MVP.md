# CortexSim MVP — launch definition

> **Living document.** Update it in the same pass that changes the thing it
> describes. Counts here are **generated**, never hand-typed — run
> `make ground-truth` and cite `docs/reference/ground-truth.json`. Hand-typed
> numbers in this repo have drifted 30–90% before, which is why that generator
> and its CI gate exist.
>
> **Status: NO-GO to publish · GO for supervised internal pilot on a fresh jumpbox.**
> Last verified 2026-08-31 against `main`.

---

## 1. What MVP means here

MVP is **not** a feature list. It is one sentence, and every criterion below
exists only to make it checkable:

> A Palo Alto Domain Consultant who has never seen this repo lands on the public
> GitHub page from a customer-lab jumpbox, and gets from clone → running app →
> deployed agent → launched simulation → a result they can read **honestly**,
> without reading source and without hitting a false promise.

The last word carries the weight. This engine's defining failure mode is
**something that did not happen presenting as success** — an unrun step, an
absent tool, a phantom agent — because in a POV report an absent detection reads
as *"Cortex missed it"*: a manufactured false negative on the customer's own
stack, in a document a consultant shows that customer.

Every launch criterion is therefore either "the operator can do the thing" or
"the operator cannot be misled about whether the thing happened."

---

## 2. Launch criteria

Each row is verified by a **command**, not by prose. If the command is not run,
the row is not met.

### 2.1 Journey — the stranger on the jumpbox

| # | Criterion | Verify with | Status |
|---|---|---|---|
| J1 | Public repo, README opens with a truthful quickstart, not marketing | read `README.md` top | ✅ |
| J2 | README contains **no promise the repo cannot keep** (no ghost image, no unbacked tag) | `grep -n "ghcr.io\|:vX.Y.Z" README.md` | ✅ |
| J3 | Counts in operator docs match the generator | `make check-ground-truth` | ✅ gated in CI |
| J4 | App reaches healthy from a clean state | `./scripts/dev-up.sh` → `/api/health` | ✅ |
| J5 | Agent enrollment one-liner is documented | `grep -c "api/agents/install" README.md` | ✅ |
| J6 | A **re-install** cannot print success for an agent that never polls | installer e2e suite (9/9) | ✅ fixed |
| J7 | Simulation launches, pull and push | `deploy/tier-d/run-tier-d.sh` | ✅ |
| J8 | A hung run is diagnosable by an operator | `/api/health` → `task_queue`, documented in README | ✅ |
| J9 | An **unrun step cannot read as a Cortex miss** | Tier-D `classify.py` ENGINE/ENVIRONMENT/TTP | ✅ |

### 2.2 Release surface

| # | Criterion | Status |
|---|---|---|
| R1 | `CHANGELOG.md` exists with a 0.1.0 section | ✅ |
| R2 | `v0.1.0` tag exists locally, pointing at the shipped HEAD | ⚠️ **re-point after the latest fixes** |
| R3 | Image builds and **parity-checked** (ships assertions, spec, agent-dist ×5, rust-dist, static) | ✅ |
| R4 | Publish hand-off documented with exact commands | ✅ `docs/release/PUBLISH-v0.1.0.md` |
| R5 | Tag / image / release **published** | ❌ **operator's trigger — see §4** |

### 2.3 Engineering gates

| Gate | Command | Current |
|---|---|---|
| UI | `cd ui && npx vitest run` | **78 files / 834 tests** |
| UI build | `cd ui && npm run build` | ✅ |
| Go beacon | `cd agent && go test ./...` | 4 packages ok |
| Backend | pytest in the prod image | 4828 passed |
| Corpus + refs + ground truth | `make validate` | ✅ exits 0 |

### 2.4 Counted ground truth

Generated — do not edit by hand. Source: `docs/reference/ground-truth.json`.

```
scenarios 177 · cards 175 · planes 16 · adapters 91
assertions 22 · eal_plugins 21 · iac_modules_aws 11 · routes 127
UC/TC evidenced 90/266   (DET/HNT 70/107)
```

---

## 3. Open items

### Blocking publish

- **R5 — nothing is published.** Tag, image and release are prepared locally and
  deliberately unpushed. See §4.
- **R2 — re-point `v0.1.0`.** It has already been wrong once: it pointed three
  commits behind HEAD, excluding the entire README rewrite and all three
  bring-up fixes. Publishing it then would have shipped the pre-pass front door.
  Re-point after the current fixes and re-verify before pushing.
- **`release.yml` `lint-shell` fails.** It runs bare `shellcheck` at default
  `style` severity and exits 1, so the tag-push → CI publish route is broken.
  **Use the manual publish route** in §4 until this is fixed.

### Not blocking, carried

- **S-13 tier disagreements grew 105 → 110** from the merged DLP scenarios' own
  metadata. Advisory only, and this class of warning is deliberate — but the 5
  new ones are unreviewed.
- **Contrast guard covers 5 of 13 destination stylesheets.** It no longer
  *claims* more than it checks (a vacuity canary now fails a stub), but 8 sheets
  remain uncovered.
- **Dark `--tx3`** falls short on `--s2`/`--s3` (4.33 / 3.99). Measured and
  stated truthfully in the token file.
- **Two designer-token deviations** (`--tx3`, `--warn`, light only) await
  sign-off. Both recorded in `docs/design/`.
- **`StackCoverageView`** intensity encoding changed; count numeral is now the
  primary channel. Wants an operator eye.

---

## 4. Publishing — the operator's trigger

No agent may push a tag, push an image, or create a release. These are yours:

```bash
cd /home/henry/Github/cortex-pov-engine
export DOCKER_CONTEXT=default

# 0. Re-point the tag at the verified head (R2), then confirm it moved
git tag -f -a v0.1.0 -m "CortexSim v0.1.0" HEAD
git log -1 --oneline v0.1.0

# 1. Push the tag
git push origin v0.1.0

# 2. Multi-arch image  (NOTE: use the manual route — release.yml lint-shell fails)
docker buildx create --use --name cortexsim-release --driver docker-container
docker buildx inspect --bootstrap
docker buildx build --platform linux/amd64,linux/arm64 -f core/Dockerfile \
  -t ghcr.io/hankthebldr/cortexsim:v0.1.0 \
  -t ghcr.io/hankthebldr/cortexsim:latest --push .

# 3. GitHub release
gh release create v0.1.0 --title "CortexSim v0.1.0" \
  --notes-file docs/release/NOTES-v0.1.0.md
```

**Push `main` first** — 103 commits are local-only, so the tag would otherwise
point at objects the remote does not have.

---

## 5. Honest limits — must survive every rewrite

These belong in front of any consultant before they walk into a lab. They are
not caveats to soften; they are the difference between a credible POV and a
false claim about a customer's coverage.

- **`tenant-verified` is 0.** Nothing in this repo has executed against a live
  Cortex tenant. Every green comes from an injected transport. **Authored is not
  proven** — never report the two as one number.
- **A bare Ubuntu target cannot run this corpus.** `www-data` ships `nologin`,
  so the identity harness dies in 7 ms and the run reads *"failed"* having
  executed nothing. `deploy/tier-d/Dockerfile.target` is what a target needs.
- **141 of 654 steps are `echo`/`printf`/`touch`-only** while declaring
  `expected_detections`. A harness pointed at those measures nothing.
- **There is no authentication, by design.** The operating DC is full admin.
  Run it on a trusted, isolated lab network — the API dispatches shell commands
  and holds a credential vault.
- **A run stays `running`, not `pending`,** when its agent never polls. Check
  `/api/health` → `task_queue`.

---

## 6. Next pass

In priority order:

1. Publish (§4), then re-run the stranger dry run against the **published**
   artifact rather than a local build.
2. Fix `release.yml`'s `lint-shell` so the CI publish route works.
3. Review the 5 new S-13 disagreements from the DLP merge.
4. Extend the contrast guard to the remaining 8 destination stylesheets.
5. Close the light-palette deviations with the designer.
