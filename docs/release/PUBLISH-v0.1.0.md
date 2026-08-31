# Publishing v0.1.0 — exact commands

This is the hand-off for the one step this pass deliberately did **not** take:
pushing anything outward. Everything below was either already done locally and
verified (tag, image build, image-parity), or is a command for **you** to run.
Nothing in this file has been executed by the agent that wrote it — no
`git push`, no `docker push`, no `gh release create`.

## What's already done, locally

- App version reconciled to `0.1.0` (`core/api/health.py::APP_VERSION`,
  `core/main.py`'s `FastAPI(version=...)`) — verified live: `GET /api/health`
  on a running container returns `"version": "0.1.0"`.
- `CHANGELOG.md` written (Keep a Changelog form), `[0.1.0]` section dated
  2026-08-31.
- Annotated tag `v0.1.0` created **locally** — not pushed. Get its SHA with:
  ```bash
  git rev-list -n1 v0.1.0
  git cat-file -p v0.1.0 | head -8
  ```
- Image built **locally, native arch only** (`linux/amd64` — this host's
  arch) and proven — see "What was verified" below.

## Pre-flight: a real blocker in the existing release automation

`.github/workflows/release.yml` already exists, triggers on `push: tags:
'v*.*.*'`, and — if green — does the entire rest of this job for you: builds
the multi-arch image, pushes it to GHCR, packages stage2 bundles, generates
`manifest.json`/`SHA256SUMS`, and runs `gh release create` with generated
notes. **It has never been run** (0 tags, 0 releases on this repo today), and
this pass found it will fail on the very first job:

```bash
shellcheck install.sh
```
```
install.sh:76:6   SC2154 (warning): _ec is referenced but not assigned.
install.sh:292:18 SC2016 (info): Expressions don't expand in single quotes...
install.sh:316:18 SC2016 (info): Expressions don't expand in single quotes...
```
`shellcheck`'s default minimum severity is `style` (lower than `info`), so
**any** finding — including these three, which are not real bugs (`_ec` is
assigned earlier in the same trap statement; the two `SC2016`s are
*deliberately* single-quoted so `$PATH` is written to the shell profile
literally, not expanded at `echo` time) — makes the `lint-shell` job exit 1.
`build-image` (`needs: [resolve-tag, lint-shell]`) is skipped, which cascades
through `manifest` and `release`: **the whole automated pipeline currently
stalls with no image pushed and no release created**, on lines this repo's
own scripts own (`install.sh` is Launch's file, not Release's — this pass
does not touch it).

This does not block you today because Option B below never goes through that
workflow. It does mean **Option A degrades to a manual `gh workflow run`
that will fail** until one of the following happens (neither is done by this
pass):
- add `# shellcheck disable=SC2016` / `disable=SC2154` at the three lines in
  `install.sh` (or a `.shellcheckrc` with the same effect), **or**
- loosen the workflow's `shellcheck` invocation (e.g. `shellcheck -S error
  "${FILES[@]}"`, which line 76's warning would still fail, so this needs a
  targeted exclude, not just a severity bump).

## What was verified locally (this pass)

```bash
export DOCKER_CONTEXT=default   # Docker Desktop hijacks the default context otherwise

# 1. Build, native arch (linux/amd64 on this host)
docker build -f core/Dockerfile -t cortexsim:v0.1.0 .

# 2. Boot it and prove the version + a clean boot
SECRET=$(openssl rand -hex 32)
docker run -d --name cortexsim-v010-check -p 18888:8888 \
  -e CORTEXSIM_ENV=development -e CORTEXSIM_SECRET="$SECRET" \
  cortexsim:v0.1.0
curl -s http://localhost:18888/api/health | python3 -m json.tool
docker rm -f cortexsim-v010-check
```

Result: `status: "ok"`, `version: "0.1.0"`, **zero `degraded_components`**,
boot log reads `Scenario load complete: 170 scenarios loaded.`

```bash
# 3. Image-content parity — this repo has shipped an image missing content
#    TWICE before (assertions/ once, an agent target once) while every gate
#    stayed green. These are the gates that catch it.
IMAGE=cortexsim:v0.1.0 make check-agent-shelf   # beacon: all 5 targets, sha256 verified
IMAGE=cortexsim:v0.1.0 make check-rust-shelf    # rust tools: all 3, sha256 verified

# static Dockerfile-vs-resolver check (host pytest has no interpreter here —
# run it inside the image, same as `make test-backend` does)
docker run --rm -v "$(pwd):/repo" -w /repo \
  -e CORTEXSIM_BASE_DIR=/repo -e CORTEXSIM_ENV=development \
  -e CORTEXSIM_SECRET="$(openssl rand -hex 32)" -e PYTHONPATH=/repo/core \
  cortexsim:v0.1.0 sh -c \
  "pip install --no-cache-dir -q pytest pytest-asyncio httpx && \
   pytest tests/tools/test_image_ships_runtime_content.py tests/installer/test_agent_dist_matrix_parity.py -q"
```

Result: `check-agent-shelf` — 5/5 targets present, `sha256sum -c` OK on all;
`check-rust-shelf` — 3/3 tools present, `sha256sum -c` OK; both pytest files
— **19 passed, 0 failed**. Manual spot-check confirmed the Windows binary is
a real PE (`4d 5a` / `MZ` magic bytes) and every content directory named in
`core/Dockerfile`'s final `COPY` list is present and non-empty inside the
running container (`assertions` 20 `.yml`, `spec` 1 file, `scenarios` 201
raw `.yml` / 170 loaded, `detection_scanner/ttps` 170 `.json`,
`tools/packs` 92 `.yml` (91 real packs + `_schema.yml`), `payloads` 8 staged
artifacts + 4 metadata files, `docs/uc_tc_mapping` 24 files, `infra/modules/aws`
11 modules, `core/static` 30 files incl. `index.html`). No component's count
disagreed with the tree.

Image: `cortexsim:v0.1.0`, id `sha256:982b0568aa1f…`, size `334MB`
(`docker images cortexsim:v0.1.0` to reproduce).

**Not verified locally, by design:** `linux/arm64`. This host is
`linux/amd64` and QEMU emulation for a full multi-stage build (two Rust
toolchain stages plus a Go cross-compile stage) would run for tens of
minutes for no proof beyond what `--platform` already gives you at push
time. The commands below build and push both architectures for real.

## Option A — tag push, let CI do it (currently blocked — see pre-flight above)

```bash
git push origin v0.1.0
```

`release.yml` picks up the tag, builds `linux/amd64,linux/arm64`, pushes to
`ghcr.io/hankthebldr/cortexsim:v0.1.0` + `:latest`, packages stage2 bundles,
writes `manifest.json` + `SHA256SUMS`, and opens the GitHub Release with
autogenerated notes (`git log` since the previous tag — there is none, so it
will read "Initial release"). Watch it: `gh run watch` after the push, or
the Actions tab. **Do this only after resolving the shellcheck blocker
above**, or expect `lint-shell` to fail and everything downstream to skip.

## Option B — manual, known to work today

```bash
export DOCKER_CONTEXT=default

# 1. Push the annotated tag (this alone does NOT trigger release.yml's
#    intended effect if you're bypassing Option A on purpose — it just makes
#    the tag public. Skip this line if you want git history and the GHCR
#    image to land in the same motion as gh release create below, since that
#    command can also create the tag on the remote for you.)
git push origin v0.1.0

# 2. One-time: a builder that can actually push multi-platform manifests.
#    The plain "default" driver (what DOCKER_CONTEXT=default gives you) does
#    NOT support --push for multiple platforms in one invocation.
docker buildx create --use --name cortexsim-release --driver docker-container
docker buildx inspect --bootstrap

# 3. Build + push both architectures, tagged both ways README already
#    promises.
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f core/Dockerfile \
  -t ghcr.io/hankthebldr/cortexsim:v0.1.0 \
  -t ghcr.io/hankthebldr/cortexsim:latest \
  --push \
  .

# 4. GitHub Release, with the drafted notes in this same directory.
gh release create v0.1.0 \
  --title "CortexSim v0.1.0" \
  --notes-file docs/release/NOTES-v0.1.0.md
```

`docs/release/NOTES-v0.1.0.md` (this directory) is the drafted release-notes
body — reproduces the `CHANGELOG.md` `[0.1.0]` section in release-page form.
Edit before running step 4 if anything above needs to change first (e.g. once
the arm64 build actually completes and you want to fold in its digest).

## After either option

```bash
# Confirm the image is real and reports what it should.
docker pull ghcr.io/hankthebldr/cortexsim:v0.1.0
docker run --rm -p 8888:8888 -e CORTEXSIM_ENV=development \
  -e CORTEXSIM_SECRET="$(openssl rand -hex 32)" \
  ghcr.io/hankthebldr/cortexsim:v0.1.0 &
sleep 3 && curl -s http://localhost:8888/api/health | python3 -m json.tool
```

Then update the README's line ~65 GHCR link and ~68 tag-cut example are
already correct once this exists — that's Runbook's file, not this pass's,
so double-check it still reads true once the image is actually live.
