# The Rust tool shelf — the contract

> **Status (2026-08-05, rust-bake pass — BUILT, EXECUTED, and NOT yet servable
> over HTTP.)** All three tier-2 Rust tools now build as **static-musl,
> zero-`DT_NEEDED`, static-pie** `linux/amd64` binaries and every one of them has
> been **started on four clean containers with `--network none`**. What does
> *not* exist yet is the serving endpoint: `GET /api/tools/binary/{tool}` is
> specified in §5 but lives in `core/`, which this pass may not edit. Until that
> lands, the bytes are reachable by `make rust-dist` and by copying
> `rust-dist/` — **not** by a DC calling an API. That gap is stated here rather
> than only in a handoff note, because this repo has shipped "green while
> proving nothing" before and a doc that implies a live endpoint is exactly how.

---

## 0 · The one-paragraph problem

`sources/signalbench`, `sources/ackbarx` and `sources/xdrtop` are **tier-2 git
submodules**. Their only distribution story was
`core/tools/registry.py`'s `build_cmd: "cargo build --release"` — executed **on
the customer jumpbox**. That needs a Rust toolchain *and* crates.io egress on
the target host, which is precisely what a default-deny enterprise network
blocks and precisely the problem `agent-dist/` already solved for the Go beacon
and the payload shelf solved for tier-4 tools. A step whose tool never arrived
runs anyway, produces no detection, and the absent detection reads in a POV
report as *"Cortex missed it"* — a manufactured false negative on the customer's
stack. This shelf is the Rust half of the same answer.

---

## 1 · Two defects this pass found, both live, both invisible

### 1.1 `signalbench` has **never** been buildable with its declared recipe

`sources/signalbench/src/techniques/software.rs:19`:

```rust
static PACEMAKER_BINARY: &[u8] = include_bytes!("../../embedded_binaries/pacemaker_helper");
```

`embedded_binaries/` **is not in the upstream git tree**. It is *produced* by
building the separate `helpers/pacemaker` crate and copying its output in —
exactly what upstream's own `.github/workflows/build-binary.yml` does. A bare
`cargo build --release` therefore fails:

```
error: couldn't read `src/techniques/../../embedded_binaries/pacemaker_helper`
error: could not compile `signalbench` (lib) due to 1 previous error
```

So `STATIC_TOOL_REGISTRY["signalbench"]["build_cmd"]` **cannot ever have
worked**, for anyone, since the tool was added. Nothing noticed because nothing
cheap ever looked: there is no `tools/packs/signalbench.yml`, so
`scripts/check-adapter-sources.sh` and the CI `adapters` job never examined it.

The failure message names a file nobody can find rather than the crate that
produces it, which is *how* it stayed invisible. Both the build recipe and the
`--check-recipe` gate now name the helper explicitly.

### 1.2 `xdrtop` links OpenSSL dynamically unless forced not to

`sources/xdrtop/Cargo.toml`:

```toml
reqwest = { version = "0.12", features = ["json"] }   # no default-features = false
```

`default-tls` → `native-tls` → `openssl-sys`. A plain build declares
`libssl.so.3` and `libcrypto.so.3` and dies on any host without OpenSSL 3 —
the exact `error while loading shared libraries` failure this shelf exists to
remove. The clean fix is `rustls-tls`, but that means **editing a submodule**,
which CLAUDE.md forbids. `OPENSSL_STATIC=1 OPENSSL_NO_VENDOR=1 OPENSSL_DIR=/usr`
against alpine's `openssl-libs-static` gets the same outcome **from the
outside**, with no source change.

`ackbarx` already uses `default-features = false, features = ["rustls-tls"]` and
`signalbench` has no TLS stack, so neither pulls OpenSSL.

---

## 2 · The target matrix: `linux/amd64` only, said out loud

Rust does not cross-compile for free the way Go does. Go needs one host and
`CGO_ENABLED=0`; Rust needs a std for each triple, a linker, and — for a static
binary — musl. **A smaller matrix that genuinely runs beats a wide one that
half-fails in front of a customer**, so exactly one triple ships:

| triple | tools | proven |
|---|---|---|
| `x86_64-unknown-linux-musl` | signalbench, ackbarx, xdrtop | executed on ubuntu 22.04/24.04, debian 12-slim, alpine 3 |

### Why every other target is absent

Each reason is written to **stand alone**, because the serving 404 quotes
exactly one of them with no other context.

| target | blocked by |
|---|---|
| `linux/arm64` | `xdrtop`'s `openssl-sys` needs an aarch64 OpenSSL. An x86_64 alpine has no aarch64 `openssl-libs-static`, and the openssl crate's `vendored` feature would require editing `sources/xdrtop/Cargo.toml` — a submodule. `ackbarx` and `signalbench` *are* reachable on arm64, but shipping 1 of 3 — and unprovable, since there is no qemu binfmt to execute an arm64 binary here — is the wide-matrix-that-half-fails the design rejects. **arm64 widens only behind an execution gate, never on a build-only pass.** |
| `darwin/*` | `signalbench` is Linux-only by design (reads `/proc`, links `libc`); `ackbarx` is a Linux SNMP daemon. Cross-building Mach-O from Linux additionally needs a non-redistributable Apple SDK. |
| `windows/amd64` | No upstream Windows build exists for `signalbench` or `ackbarx`; `xdrtop` is a terminal UI shipped upstream only as a `.zip`. Use the Go beacon's push mode on Windows targets. |

These strings live in `UNSUPPORTED[]` in `scripts/build-rust-dist.sh`, are
copied into `rust-dist/MANIFEST.json` as `unsupported_targets[]`, and are
asserted non-trivial (>40 chars, one per target) by
`tests/tools/test_rust_dist.py`. **A DC asking "where is the arm64 build?" gets
a sentence, not silence.**

---

## 3 · The pipeline

```bash
make rust-dist                                  # → ./rust-dist/
scripts/build-rust-dist.sh xdrtop               # one tool
scripts/build-rust-dist.sh --check-recipe       # ~50 ms, no compiler, no docker
OUT_DIR=/tmp/rd scripts/build-rust-dist.sh
RUST_BUILDER_IMAGE=rust:1.90-alpine ...         # pinned default
```

### It requires Docker and refuses without it

A host `cargo build --release` produces a **glibc-dynamic** binary. Handing a DC
that artifact is worse than refusing, because it dies on the customer jumpbox
and the DC finds out in front of the customer. The refusal names the missing
dependency **and** the way out (copy a `rust-dist/` from a machine that has
Docker — the bytes are portable, which is the entire point).

### It cannot write into a submodule

Two mechanisms, one of them mechanical:

1. `sources/` is mounted **`:ro`** into the builder. This is enforced by the
   kernel, not by convention — and it matters, because `signalbench`'s build
   *must* write `embedded_binaries/` somewhere.
2. Each tree is copied to `/work/<tool>` inside the container before building,
   so `target/` and the generated `embedded_binaries/` land in scratch.
   `target/` is excluded from the copy: a host GNU build leaves hundreds of MB
   there and none of it is valid for the musl triple.

Verified after a full run: `git -C sources/<tool> status --porcelain` shows no
new files, and `sources/signalbench/embedded_binaries/` does not exist.

### Nothing is published that was not proven

Per artifact, inside the builder, **before** it reaches `OUT_DIR`:

* `readelf -d` must report **zero** `NEEDED` entries → else hard fail.
* the binary must **start** (`--version`, falling back to `--help`) → else hard
  fail with *"A binary that exists is not a binary that runs."*

This is the gate that makes a silent regression impossible: an alpine bump that
moves OpenSSL, or an `openssl-sys` release that changes the env-var contract,
would otherwise revert `xdrtop` to a dynamic link **without failing the build**.

---

## 4 · Provenance — `MANIFEST.json`

The payload shelf's `pin` tells a DC *which* tool they shipped. A locally-built
binary needs the same guarantee, keyed on the **submodule gitlink** instead of a
release tag.

```jsonc
{
  "$generated": { "by": "scripts/build-rust-dist.sh", "schema": 1 },
  "builder_image": "rust:1.90-alpine",
  "target": "x86_64-unknown-linux-musl",
  "tools": [
    {
      "tool": "signalbench",
      "version": "signalbench 1.8.1",
      "submodule_commit": "85d4e1d85d1c29370fd1d37d2bcfe014655c247a",
      "submodule_tag": "v1.8.1",
      "filename": "cortexsim-tool-signalbench-linux-amd64",
      "sha256": "86245ec8…",
      "size_bytes": 13441664,
      "static": true,
      "verified_exec": true
    }
  ],
  "unsupported_targets": [ { "target": "linux/arm64", "tools": ["xdrtop"], "reason": "…" } ]
}
```

Rendered **byte-deterministically** — fixed key order, 2-space indent, **no
timestamp, no hostname, no builder username** — so two builds of the same inputs
are diffable. `builder_image` is **pinned**, never `rust:alpine`.

**Measured reproducibility:** two independent full runs produced **bit-identical
binaries** (all three sha256 digests unchanged), so the pin is real rather than
aspirational.

`submodule_tag` is `null` for `ackbarx` — its gitlink `e677e70` carries no tag.
That is recorded rather than guessed; a manifest that invents a plausible
version is worse than one that admits it does not know. See §7 F5.

### Staleness must be visible, never silent

A `rust-dist/` built from an older gitlink serves a DC a **different tool** than
the source tree says — the silent-substitution failure the shelf's pinning
exists to prevent. Three places catch it:

* `build-rust-dist.sh --check-recipe` → **FAIL** naming both commits.
* `tests/tools/test_rust_dist.py::test_shelf_is_not_stale_relative_to_the_checkout`.
* the serving endpoint (§5) sets `stale: true` with a remediation string but
  **still serves** — a stale binary that runs beats no binary at all; it just
  must never look current.

---

## 5 · Distribution — a sibling `rust-dist/`, served with the **agent's** idiom

### Not the payload shelf, and the reasons are in the shelf's own schema

1. **The shelf's `install.artifact` describes a *download*, not a *build*.** It
   requires `url` (TA-03 rejects non-http(s)), a `pin`, and a `sha256` known
   *before* the fetch. A locally-built binary has **no upstream URL** and its
   digest is a function of the toolchain, not of a pin. The only expressible
   form is `pin.type: none` + `waiver_reason`, which
   [`payload-shelf.md`](payload-shelf.md) §3 calls "a **dev-only** state, never
   valid in an image that goes to an engagement."
2. **TA-01 hard-rejects `install.artifact` on `tier != 4`.** Keep the rule — but
   note its stated *rationale* is **wrong for these three**: it says tier-1/2
   trees "have no egress problem for the shelf to solve", and `cargo build
   --release` on a jumpbox is an egress problem in every sense. See §7 F4.
3. **Upstream releases cannot rescue it.** Only `signalbench` has a clean match
   (tag == gitlink, bare musl asset). `xdrtop` ships `.deb`/`.tar.gz`/`.zip` and
   **TA-08 rejects `kind: archive` outright**. `ackbarx`'s gitlink is
   **untagged** and its assets are glibc-only, so no `pin` could honestly
   reference the source we ship. One-of-three on the shelf creates the third
   idiom this repo forbids.

### This is a third *mount* of one idiom, not a third idiom

```
/api/agents/binaries          /api/shelf/payloads          /api/tools/binaries
/api/agents/binary            /api/shelf/payload/{name}    /api/tools/binary/{tool}
/api/agents/binary/sha256     /api/shelf/payload/{n}/sha256 /api/tools/binary/{tool}/sha256
```

Plural collection, singular artifact, `FileResponse`, an `X-CortexSim-*-SHA256`
header, `Cache-Control: no-store`, and a 404 naming the directory **and** the
command that fills it. No new schema, no new pin model, no second integrity
story.

### Integrity

* The digest is **computed on SimCore from the bytes on disk at request time**,
  never read from `SHA256SUMS`. `SHA256SUMS` is for humans and `sha256sum -c`
  only — this preserves the agent script's guarantee that *a stale sums file can
  never make a tampered binary verify*.
* The consumer **carries** the digest (download header, or `/sha256` first) and
  verifies **before execution**.
* Mismatch is a **hard fail** — no retry, no "continue anyway".

**Not yet implemented.** `core/api/tools_dist.py` does not exist and
`core/main.py` does not register it — `core/` belongs to a concurrent workflow
this pass may not edit. That workflow has already written
`tests/tools/test_rust_dist_route_registration.py`, which skips while the module
is absent and fails the moment it lands unmounted. **Route count moves 133 → 136
when it lands, and acceptance is a live `curl /api/tools/binaries` returning
200 — never a passing unit test**, because this repo has shipped a whole feature
whose launch path was never called while 4368 tests stayed green.

---

## 6 · Gates

| gate | cost | when |
|---|---|---|
| `scripts/build-rust-dist.sh --check-recipe` (inside `scripts/check-adapter-sources.sh` → CI **`adapters`**, `make validate`) | ~50 ms | **every** push/PR |
| `make check-rust-recipe` | ~50 ms | local |
| CI **`rust-dist`** — build + `sha256sum -c` + **execute on ubuntu:22.04 and alpine:3 with `--network none`** | ~4 min | only when `sources/**`, `scripts/build-rust-dist.sh`, `core/tools/registry.py`, `core/Dockerfile` or the workflow changes |
| `make check-rust-exec` | ~15 s | local, after `make rust-dist` |
| `make check-rust-shelf` | ~2 s | asserts the **built image** serves the matrix |
| `tests/tools/test_rust_dist.py` (24 tests) | <1 s | backend suite |

The always-on half is deliberately in `adapters` and **not** in the
`paths:`-filtered `rust-dist` job. `payload-shelf.md`'s own warning applies: *a
job that can be skipped reads exactly like a passing one*. `rust-dist` is
**not** a required check.

The exec step is the point. A build-only job would have gone green on a binary
that segfaults. **Had `rust-dist` existed, signalbench's broken `build_cmd`
would have been caught at the commit that pinned v1.8.1.**

`check-rust-shelf` mirrors `check-agent-shelf` and asserts the **IMAGE**, never
the checkout — `docker run --entrypoint sh` with **no `-v`**, deliberately. A
host mount would let a local `make rust-dist` mask a `rust-builder` stage that
never landed, which is exactly how the beacon shipped 5 targets from the script
and 4 from the image for months.

---

## 7 · Findings — files this pass may not edit

**F1 · `core/Dockerfile`'s `agent-builder` omitted `windows/amd64` — FIXED
mid-pass by a concurrent workflow, and still unverified in an image.** The loop
was `for t in linux/amd64 linux/arm64 darwin/amd64 darwin/arm64` while
`scripts/build-agent-dist.sh` had five targets. Every deployed image therefore
served 4 beacons, `?os=windows` 404'd, and the PowerShell installer refused
before enrolling — stranding the 71 scenarios that declare
`platforms: [windows]`. **Measured during this pass:** `make agent-dist` emitted
5 binaries (`SHA256SUMS` 478 B) while
`docker run --entrypoint sh cortexsim:dev -c 'ls /app/agent-dist'` showed **4**
(`SHA256SUMS` 378 B, no `.exe`). The Dockerfile loop now carries all five and
`tests/installer/test_agent_dist_matrix_parity.py` guards the divergence.
**Still owed: a rebuild plus `make check-agent-shelf`** — the fix is in the
source tree, not yet in any built image, and this repo's whole point is that
those are different claims.

**F2 · CLAUDE.md's Windows paragraph is stale.** It claims
`build-agent-dist.sh` "still omits `windows/amd64` from its `TARGETS` array" —
**false**, it is present and builds (verified: `make agent-dist` emits 5
binaries including `cortexsim-agent-windows-amd64.exe`, 5,797,888 B). It claims
`core/api/agents.py::_WINDOWS_PREFLIGHT_UNAVAILABLE` "still asserts the executor
cannot compile for GOOS=windows" — **also false**; the comment already says the
opposite. The *conclusion* ("not servable") is accidentally right, but for the
Dockerfile reason in F1, not the reason stated.

**F3 · Both Rust `run_template`s in `core/tools/registry.py` are wrong**, proven
by execution on a clean ubuntu:22.04:

| tool | declared template | result |
|---|---|---|
| signalbench | `--technique {mitre_id} --count {count} --output json` | `error: unexpected argument '--technique' found` |
| ackbarx | `--listen-port 162 --forward-url {xsiam_endpoint}` | `error: unexpected argument '--listen-port' found` |
| xdrtop | `{binary}` | fine |

Real CLIs: `signalbench <COMMAND>` where `COMMAND ∈ {list, run, category,
voltron}` — `run` takes **positional** techniques (`signalbench run T1082`) and
there is no `--count` and no `--output json`; `ackbarx` is **config-file
driven** (`-c <FILE>`, `--daemon`, `--generate-config`). Verified working:
`signalbench run T1082 --dry-run` exits 0 and creates
`/tmp/signalbench_system_info`; `ackbarx --generate-config` writes `config.json`.
`signalbench`'s `build_cmd` is also a lie (§1.1) and must become the two-phase
recipe or point at `rust-dist/`. Patch in the handoff.

`--chain` is worth wiring later: it builds a real parent/child process tree,
which is exactly what the causality contract wants. Out of scope here.

**F4 · `adapter_loader.py`'s TA-01 message is factually wrong about tier 2.** It
says tier-2 trees have no egress problem "for the shelf to solve." They do.
Keep the rule; fix the sentence to say the shelf's *schema* cannot describe a
locally-built artifact, and point here. Leaving it is how the next reader
concludes there is nothing to solve.

**F5 · `ackbarx`'s gitlink is untagged** (`e677e70` on `main`; `git describe` →
"No names found"). `signalbench` and `xdrtop` sit on clean release tags. Pinning
it to `v0.6.2` would let `MANIFEST.json` record a meaningful `submodule_tag` —
but that changes a gitlink, so it is Henry's call, not the builder's.

**F6 · Scope boundary, stated so it is not over-promised.** This pass makes the
binaries **exist, be static, be executed, and be reproducible**. The consumer
today is SimCore's own `instantiator` (a subprocess on the SimCore host). Handing
one to an **enrolled beacon** as a staged task artifact is the shelf's
`compose()` path, which [`payload-shelf.md`](payload-shelf.md) documents as
**currently broken** (`Orchestrator._handle_pull` never calls `compose()`;
producer/consumer field-name mismatch). **Beacon staging is deliberately not
wired here** — it follows once `compose()` works.

**F7 · A concurrent pass added a `rust-builder` stage to `core/Dockerfile`.** It
uses the same recipes, the same `cortexsim-tool-<tool>-linux-amd64` naming and
the same static+exec gate, and `COPY --from=rust-builder /out/ /app/rust-dist/`.
The two paths are complementary, not duplicative — the Dockerfile stage fills
`/app/rust-dist` for a containerised deploy, this script fills `./rust-dist` on
the host for CI's exec proof, a host-run dev SimCore, and air-gapped byte
transfer. **As of this writing that stage is written but has never been built**:
`docker run --entrypoint sh cortexsim:dev -c 'ls /app/rust-dist'` returns *No
such file or directory*. `make check-rust-shelf` is the gate for it. Do **not**
add `COPY rust-dist/ /app/rust-dist/` alongside it — the two would collide.

---

## 8 · Measured results

```
builder: rust:1.90-alpine   target: x86_64-unknown-linux-musl   cold: 4m00s
```

| tool | version | size | linkage |
|---|---|---:|---|
| signalbench | 1.8.1 | 13,441,664 B | ELF static-pie, 0 `NEEDED` |
| ackbarx | 0.6.2 | 8,488,800 B | ELF static-pie, 0 `NEEDED` |
| xdrtop | 2.1.1 | 12,669,000 B | ELF static-pie, 0 `NEEDED` |

**Execution proof — 12/12, `--network none`, `--platform linux/amd64`:**

| image | libc | signalbench | ackbarx | xdrtop |
|---|---|---|---|---|
| ubuntu:22.04 | glibc 2.35 | 1.8.1 | 0.6.2 | 2.1.1 |
| ubuntu:24.04 | glibc 2.39 | 1.8.1 | 0.6.2 | 2.1.1 |
| debian:12-slim | glibc 2.36 | 1.8.1 | 0.6.2 | 2.1.1 |
| alpine:3 | musl | 1.8.1 | 0.6.2 | 2.1.1 |

Beyond `--version`: `signalbench list` and `signalbench run T1082 --dry-run`
both exit 0 offline on ubuntu:22.04, and `ackbarx --generate-config` writes a
config — so these are working tools, not files that merely start.

**Negative controls** (an assertion that cannot fail proves nothing):

| control | result |
|---|---|
| `helpers/pacemaker` removed | `--check-recipe` → **FAIL**, rc=1, message names the crate and the `include_bytes!` line |
| registry declares a cargo tool with no recipe | `--check-recipe` → **FAIL**, rc=1 |
| run on a host with genuinely no Docker (inside `ubuntu:22.04`) | refuses, rc=1, explains why a host build is not attempted |
