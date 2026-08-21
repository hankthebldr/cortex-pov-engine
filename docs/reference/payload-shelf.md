# The Payload Shelf — the contract

> **Status (2026-08-05, end of the four-track pass — VERIFIED, and NOT shippable
> yet).** Machinery shipped and green. **8 adapter packs declare an
> `install.artifact`** (`TOOL-LINPEAS`, `TOOL-PSPY`, `TOOL-SUID3NUM`,
> `TOOL-LSE`, `TOOL-LINENUM`, `TOOL-DEEPCE`, `TOOL-TRAITOR`,
> `TOOL-AMICONTAINED`); `payloads/sources.json` is fully derived and
> `unbound[]` has reached **zero**. Catalog: **91 adapters, 0 rejected, 8
> staged**, of **56 tier-4 packs** — so **48 of 56 tier-4 tools still fetch
> from the public internet on the target host at run time**.
>
> **Tier-4 triage complete 2026-08-06 — the count did NOT move, and that is the
> correct outcome (§11).** All 48 remaining packs now declare
> `install.artifact_exempt` with a DC-facing reason, enforced by **TA-13**
> (a tier-4 pack declaring neither an artifact nor an exemption is REJECTED, so
> "nobody got to it" is structurally unrepresentable). **Zero packs were
> wired**, because the measured finding is that *not one of the 48 is a bare
> single file at a pinned URL* — the entire remaining opportunity is
> archive-shaped, and `kind: archive` is rejected because **no consumer can
> unpack one** (the beacon's `Artifact` struct has no `Kind` field at all).
> Report this as "**8 of 56, 48 reasons, an 8-item backlog**", never as
> coverage.
>
> **Three defects block the end-to-end path Henry asked for.** They are listed
> here, not only in §9, because this file is the contract the other tracks read
> and a stale "shipped" header is how this repo has shipped green-but-broken
> before:
>
> 1. **`Orchestrator._handle_pull` never calls `compose()`.** `Task.artifacts`
>    is always empty in production. Verified by launching `SIM-EDR-022` in pull
>    mode against a real enrolled beacon: no staging phase ran at all. §9 item 5.
> 2. **The producer and the consumer disagree on field names.** `compose()`
>    emits `url` (absolute) and `dest_path`; the beacon's `ArtifactSpec`
>    (`agent/beacon/artifact.go`) requires `path` (server-relative) and `dest`.
>    Feeding `compose().to_dict()["artifacts"]` straight into `Task.artifacts`
>    makes the beacon refuse every artifact with `ARTIFACT_SPEC_INVALID`. The
>    projection function the orchestrator docstring calls "projected onto the
>    beacon's wire contract" **does not exist**. §9 item 5a.
> 3. **`compose()` silently drops an `adapter_ref` that is not in the catalog** —
>    `continue` plus a log line. It does **not** land in `unstaged_adapters[]`,
>    so an unloaded catalog (the `COPY tools/` failure this repo has already
>    shipped once) yields `artifacts: []`, `unstaged_adapters: []`,
>    `air_gapped: true`, `warnings: []` and a citable `composition_id` — a
>    perfectly green plan that stages nothing. §9 item 12.
>
> A fourth, console-only: **`core/api/payloads.py::list_declared_artifacts`
> calls `declared_artifacts()` with no `scenarios=`**, so the live API reports
> `used_by = "(no scenario references this adapter yet)"` for `TOOL-LINPEAS`
> while the generated `payloads/sources.json` correctly says `SIM-EDR-022`. The
> console reads the API and tells a DC the opposite of the truth. §9 item 13.
>
> What IS proven end to end: with the missing projection applied by hand, a real
> beacon on a host with **no public-internet egress** fetched linpeas/pspy64/
> suid3num.py from `/api/shelf/payload/{name}`, verified each against the digest
> carried in the task, and ran real LinPEAS as `www-data` under an `apache2`
> CGO (steps 1–3 exit 0). The shelf, the serving endpoints, the beacon's
> staging phase and the refusal taxonomy all work.

Three tracks author against this file: the **adapter/content** track (declares
artifacts on packs), the **beacon** track (fetches and verifies on a customer
endpoint), and the **console** track (stages and composes). It is the contract,
not a design sketch — where it says "refuses", the code refuses, and there is a
test.

---

## 0 · The one-paragraph design

A tier-4 adapter pack may declare **one staged artifact** (`install.artifact`).
`payloads/sources.json` is **generated** from those declarations.
`scripts/build-payloads.sh` stages the bytes onto the DC's own SimCore. One
resolver — `core/engine/payload_shelf.py::compose()` — walks
`scenario → adapter_ref → pack → artifact → shelf` and **refuses at compose
time** with a structured error if anything is missing or if the bytes on the
shelf disagree with the pack's pin. The composition carries a digest recomputed
from the shelf bytes, and the consumer verifies against **that value, carried
in** — never one fetched from the same server it is trusting. The **destination
path** (never the shelf key) is overridable, which is what makes a rename
negative-control expressible.

### Why the shelf exists at all

All 50 tier-4 packs install their tool from the **public internet, on the target
host, at dispatch** (`command -v hydra || apt-get install -y hydra`). The
customers who buy Cortex run default-deny egress, so that is the first thing
their network blocks. A step whose tool never arrived **runs anyway**, produces
no detection, and the absent detection reads in a POV report as *"Cortex missed
it"* — a manufactured false negative on the customer's stack, in a document a DC
shows a customer. That is worse than any crash, and every paranoid-looking rule
below is downstream of that sentence.

### Boundaries fixed up front

| boundary | why |
|---|---|
| **The shelf serves the beacon, the K8s pod and the console. It does NOT serve bash/PowerShell bundles.** | Those carry the no-SimCore-at-runtime invariant that 169 scenarios depend on and that `tests/engine/test_push_generator_invariant.py` freezes byte-for-byte. `consumer="bundle"` is a hard `400 BAD_CONSUMER`. A bundle that needs a tool must EMBED its bytes. |
| **`/api/k8s/*` stays mounted forever.** | Every manifest this engine has emitted hard-codes `$CORTEXSIM_SERVER/api/k8s/payloads` and `/api/k8s/payload/$PN` inside `k8s_manifest._SERVED_FETCH`. Those files live in customers' GitOps repos and tickets. `/api/shelf/*` is an **additional mount of the same handler functions** — not a move, not a redirect. A redirect would make a stale manifest silently keep working, so the drift would never be discovered. |
| **Only single-file artifacts are stageable.** | `kind: archive` is REJECTED (TA-08). No consumer can unpack one — **re-verified 2026-08-06, see §11**. Accepting the value would let a pack declare a tool that silently never lands. |
| **48 of the 56 tier-4 packs are not shelf-able, and each now says why.** | Counted, not estimated: 18 `apt`/`yum`, 12 `pip`/`gem`, 7 `git clone` of a tree, 6 archive-only, 2 that fetch a data feed at run time, and one each for licence, multi-file and size. They keep `runtime_install_command` and **must** declare `install.artifact_exempt` (TA-13). **Making the gap legible is the deliverable.** Closing the apt family would mean hosting a Debian mirror. |

---

## 1 · The schema — `install.artifact`

Optional block inside `install:`, **tier 4 only**. Full annotated reference:
`tools/packs/_schema.yml`. Implementation: `core/tools/adapter_loader.py`.

```yaml
install:
  binary: /tmp/linpeas.sh                 # unchanged
  runtime_install_command: "..."          # unchanged, now OPTIONAL (TA-02)

  artifact:
    filename: linpeas.sh                  # (required) THE SHELF KEY, bare filename
    url: "https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh"
    kind: file                            # (required) `file` only
    sha256: "0ea7e9ce…"                   # (required unless pin.type == none)
    pin:
      type: release-tag                   # release-tag | commit | digest-only | none
      ref: "20250601"
      waiver_reason: null                 # (required, non-empty, when type: none)
    license: MIT                          # (optional) defaults to upstream.license
    stage_path: /tmp/linpeas.sh           # (optional) DEFAULT destination on the target
    mode: "0755"
```

### Validation codes

Prefixed `TA-` and not `A-` because `core/engine/assertions.py` already owns
`A-10`..`A-24`; reusing that prefix would make `grep -r 'A-14'` return two
unrelated rules with opposite meanings.

| code | rule | severity |
|---|---|---|
| `TA-01` | `install.artifact` on a pack whose `tier != 4` | **REJECT** |
| `TA-02` | tier 4 declares **neither** `runtime_install_command` **nor** `artifact` | **REJECT** — *this RELAXES the old "tier 4 requires runtime_install_command". All 84 shipped packs satisfy it unchanged.* |
| `TA-03` | `filename` fails `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, is shelf bookkeeping, or `url` is not http(s) | **REJECT** — a name the endpoints would 400 on could be staged and never served |
| `TA-04` | `sha256` is not 64 lowercase hex | **REJECT** |
| `TA-05` | `pin.type != none` and `sha256` is empty | **REJECT** — "pinned to v1.2.3" with no digest is a claim, not a pin |
| `TA-06` | `pin.type == none` and `waiver_reason` is empty | **REJECT** — unpinned is allowed; unpinned *and unexplained* is not |
| `TA-07` | `pin.type in (release-tag, commit)` and no `ref`; or an unknown `pin.type` | **REJECT** |
| `TA-08` | `kind: archive` | **REJECT** (see boundaries) |
| `TA-09` | `stage_path` not absolute / has `..` / outside the allowlist; bad `mode` | **REJECT** |
| `TA-10` | absolute `install.binary` disagrees with `stage_path`; bare `install.binary` with no `stage_path` | **REJECT**, with **back-fill** when `stage_path` is absent |
| `TA-11` | `url` contains `/releases/latest/download/`, `/raw/main/`, `/raw/master/`, `/archive/refs/heads/`, `@main|@master|@latest` | **WARN at load** |
| `TA-12` | two packs claim one `filename` with **different** `sha256` | **REJECT the second** (same filename + same digest is fine) |

**TA-10 is load-bearing.** The artifact's landing path and what `{binary}`
renders must never disagree, or every step invoking the adapter runs a file that
is not there.

**Staging allowlist** — `/tmp/`, `/var/tmp/`, `/dev/shm/`, `/opt/cortexsim/`.
One tuple (`adapter_loader._ALLOWED_STAGE_ROOTS`, re-exported as
`payload_shelf.ALLOWED_STAGE_ROOTS`) checked at three independent gates: the
pack (TA-09), a scenario's `stage_as` (the loader track's S-19, **owed**), and a
compose-request override (`PAYLOAD_DEST_REFUSED`). A destination is a **write on
a host the DC does not own**, and a beacon frequently runs as root.

---

## 2 · The staging list is DERIVED

`payloads/sources.json` is **generated** from every pack's `install.artifact`.

```bash
python3 -m engine.payload_shelf --write     # regenerate
python3 -m engine.payload_shelf --check     # exit 1 on drift, print the diff
python3 -m engine.payload_shelf --stdout
```

The drift gate runs in-process as
`tests/engine/test_payload_shelf.py::test_the_committed_sources_file_matches_the_real_packs`.
Wire `--check` into CI's existing `adapters` job; do **not** add a thirteenth job
(a job that can be skipped reads exactly like a passing one, which is on this
repo's list of shipped failure modes).

**Why generated.** The hand-maintained list had **already drifted**: it declared
`adapter_id: TOOL-LINPEAS` for a pack that has never existed. That is the exact
failure the finding-slug work taught this repo — caught in the wild, in the file
we were about to build on.

Rendering is byte-deterministic: fixed key order, 2-space indent, trailing
newline, **no timestamp, no host, no version string** — the same rule
`generate_bootstrap` holds. A generated artifact that is committed must be
provably re-derivable, or it is a second hand-maintained list wearing a hat.

### `unbound[]` — the migration bucket

```jsonc
{
  "$generated": { … },
  "payloads": [ …DERIVED from install.artifact… ],
  "unbound":  [ …hand-authored, must reach zero… ]
}
```

`unbound[]` is the **only** hand-authored list in the file. An entry belongs
there only while an artifact is stageable but no pack declares it. It is staged
identically by `build-payloads.sh`, but **nothing can compose it** — no
`adapter_ref` resolves to it — so no consumer can be handed it. Every entry must
carry `pack_todo` naming the pack file that closes it, and must **not** carry an
`adapter_id` (a test asserts this: claiming an adapter you do not have is the
precise drift this design removes).

The file stays **committed** at the same path so a fresh clone and an air-gapped
`PAYLOAD_OFFLINE=1` build work with no generator run.

---

## 3 · Pinning

Two failure modes. They are different and operators conflate them:

* **Unpinned** (`sha256: null`) — `build-payloads.sh` records whatever it
  downloaded. Every consumer then verifies against *that* and passes. Nothing
  breaks. The DC silently ships a **different tool** than the one they tested.
  **This is the dangerous one.**
* **Pinned to a moving URL** — the build **fails loudly** the next time upstream
  ships, and any composition generated before a re-stage hard-fails. Loud,
  diagnosable, correct.

**Pinning converts a silent substitution into a loud build failure.**

Policy: every artifact carries a `pin`; `type: none` requires a
`waiver_reason` and is a **dev-only** state, never valid in an image that goes to
an engagement. `PAYLOAD_ALLOW_UNPINNED=0 ./scripts/build-payloads.sh` enforces
it — wire that into CI and any release build. The script default stays `1` so a
dev discovering a digest can still run it.

Runtime surfacing: `_warn_if_shelf_is_unpinned()` logs at boot, naming each
unpinned artifact. Verified live on this repo:

```
WARNING [api.payloads] payload shelf: 2 staged artifact(s) are UNPINNED
        (deepce.sh, linpeas.sh). Their digests were recorded from whatever was
        downloaded, so an upstream change substitutes a different tool silently.
```

---

## 4 · Endpoints

Two prefixes, **one implementation** (`_register_shelf_routes` registers the
same three handler functions on both routers).

| method | path | auth | purpose |
|---|---|---|---|
| GET | `/api/shelf/payloads` · `/api/k8s/payloads` | **always open** | inventory + reachability probe + `declared[]` |
| GET | `/api/shelf/payload/{name}` · `/api/k8s/payload/{name}` | shelf token | the bytes |
| GET | `/api/shelf/payload/{name}/sha256` · `/api/k8s/payload/{name}/sha256` | shelf token | bare hex, **humans only** |
| GET | `/api/shelf/artifacts` | open | the DERIVED declaration: staged / unstaged / unpinned |
| POST | `/api/shelf/compose` | shelf token | resolve a digest-bound plan, or 409 |
| GET | `/api/shelf/resolve/{scenario_id}` | shelf token | console preflight for one scenario |
| POST | `/api/shelf/stage` | shelf token | pull a public tool onto **this SimCore** |
| GET | `/api/k8s/bootstrap/{id}` (+`/sha256`), `/api/k8s/posture-findings` | open | genuinely K8s-specific; unchanged |
| GET | `/api/tools/binaries` | open | prebuilt **Rust tool** inventory + `unsupported_targets[]` |
| GET | `/api/tools/binary/{tool}` | open | the bytes (`X-CortexSim-Tool-SHA256`) |
| GET | `/api/tools/binary/{tool}/sha256` | open | bare hex, for the consumer's verify step |

The last three are a **third mount of this same idiom**, not a second shelf —
see *The Rust tool shelf* below for why tier-2 built binaries cannot be
`install.artifact` entries.

### The agent-binary idiom, transposed

There is **one** distribution idiom in this codebase, not two:

```
/api/agents/binaries          /api/shelf/payloads
/api/agents/binary            /api/shelf/payload/{name}
/api/agents/binary/sha256     /api/shelf/payload/{name}/sha256
```

Plural collection, singular artifact. That split also means no literal path
segment can ever shadow a payload name — `POST /api/shelf/stage` is a sibling of
`/payloads`, never a child of `/payload/{name}`.

`/payloads` is **unauthenticated in every mode**, because it is the manifest's
reachability probe. A probe that can fail for two different reasons (no route /
bad credential) sends a DC to argue with the customer's network team about an
auth problem that does not exist.

### Response headers on a download

```
X-CortexSim-Payload-SHA256: 0ea7e9…      the served bytes
X-CortexSim-Payload-Name:   linpeas.sh   which shelf entry, when dest_path renamed it
Cache-Control:              no-store
```

`no-store` is not decoration: a proxy caching a payload across a re-stage serves
stale bytes that then fail the consumer's digest check, and the resulting
message points at "an intercepting proxy" — a wrong diagnosis manufactured by
our own headers.

### Auth — stated bluntly

The shelf defaults to **open**. `CORTEXSIM_K8S_PAYLOAD_AUTH=token` +
`CORTEXSIM_K8S_PAYLOAD_TOKENS` gate the artifact, compose and stage endpoints.

**SimCore has no API authentication of any kind.**
`GET /api/agents/{id}/tasks` hands the full attack command set to anyone who
knows an agent id; `GET /api/scenarios/{id}/download?format=bash` hands a
complete self-contained attack bundle to anyone. Gating the shelf while those
are open is theatre — the bundle contains the same TTPs the tool would run. The
shelf's posture must **equal** SimCore's, not exceed it; record the repo-wide
fix as `GAP-AUTH-001` and let the shelf inherit it.

**Do not reuse the enrollment token for the beacon's fetch.** Enrollment tokens
are TTL-bounded, use-counted, redeemed once at onboarding and **not retained by
the beacon** (`enroll_agent` returns only `agent_id`). A beacon fetching an
artifact at task time, weeks into a POV, has no valid enrollment token. Wiring
the shelf to that credential produces a fetch that works on the DC's laptop in
hour one and 403s in the field in week two. If `token` mode is ever used with a
beacon, the token is baked into the systemd unit / launchd plist at install
time by `GET /api/agents/install`. **Never in the task JSON** —
`queued_tasks.payload` is plaintext in SQLite.

`_SERVED_FETCH` sends a bare `wget` with no header, so `token` mode + K8s
`delivery=served` produces a manifest that applies cleanly and 403s in the pod.
That refusal belongs in `k8s_manifest.build_objects` at generation time — see
§9, it is owed by that track.

### The Rust tool shelf — a THIRD MOUNT of the same idiom, not a third idiom

Added 2026-08-05. The three tier-2 Rust tools (`signalbench`, `ackbarx`,
`xdrtop`) had **no distribution story at all**: `core/tools/registry.py` says
`build_cmd: "cargo build --release"`, which means a Rust toolchain plus
crates.io egress **on the jumpbox** — the same default-deny problem §"Why the
shelf exists at all" is about, in a tier the shelf does not cover. They are now
baked into the image by `core/Dockerfile`'s **`rust-builder`** stage
(static-musl `linux/amd64`, ~34.6 MB of binaries) and served from
`/app/rust-dist`:

```
/api/agents/binaries    /api/shelf/payloads          /api/tools/binaries
/api/agents/binary      /api/shelf/payload/{name}    /api/tools/binary/{tool}
/api/agents/binary/…    /api/shelf/payload/{name}/…  /api/tools/binary/{tool}/sha256
```

Same shape, same `FileResponse`, same `X-CortexSim-*-SHA256` header, same
`Cache-Control: no-store`, same refusal-names-the-directory-and-the-command
rule. **No new schema, no new pin model, no second integrity story** — the
digest is computed on SimCore from the bytes on disk at request time, carried
out by the consumer, and verified before execution; mismatch is a hard fail.

**Why they are NOT `install.artifact` entries on the shelf.** The reasons are in
this document's own schema, not in taste:

1. **`install.artifact` describes a *download*, not a *build*.** It requires an
   upstream `url` (TA-03 rejects non-http(s)), a `pin`, and a `sha256` that is
   knowable *before* the fetch (TA-04). A locally-built binary has no upstream
   URL and its digest is a function of the toolchain, not of a pin. The only
   expressible form is `pin.type: none` + `waiver_reason`, which §3 calls a
   **dev-only state, never valid in an image that goes to an engagement**.
2. **Upstream releases cannot rescue it.** Only signalbench has a clean match
   (tag == gitlink, bare single-file musl asset). xdrtop publishes `.deb` /
   `.tar.gz` / `.zip` — **TA-08 rejects `kind: archive` outright, forever**.
   ackbarx's gitlink is **untagged** and its published assets are glibc-only, so
   no `pin` could honestly reference the source we ship. Putting 1 of 3 on the
   shelf and 2 somewhere else is how you *get* a third idiom.
3. **TA-01's rule is right; TA-01's stated reason is wrong.** It hard-rejects
   `install.artifact` on `tier != 4` and explains that tier-1/2 trees "do not
   fetch a tool at dispatch, so none of them has an egress problem for the shelf
   to solve." **That sentence is false for these three** — `cargo build
   --release` is an egress problem in every sense. Keep the rule (reason 1 is
   sufficient on its own); the sentence should say the shelf's *schema* cannot
   describe a locally-built artifact, and point here. Left as-is, it is how the
   next reader concludes there is nothing to solve.

**Provenance.** `rust-dist/MANIFEST.json` is the pin analogue, keyed on the
**submodule commit** instead of a release tag: `builder_image`, `target`, and
per-tool `version` / `sha256` / `size_bytes` / `static` / `verified_exec` /
`submodule_commit`. It is rendered byte-deterministically (fixed key order, no
timestamp, no hostname, no builder username). Commits arrive as
`--build-arg <TOOL>_COMMIT`; **unset, they are recorded as `null` with
`provenance: "unrecorded"`**, never as a plausible-looking value, and
`/api/tools/binaries` reports `stale: null` with the reason rather than
`stale: false`. An unprovable claim of currency is the silent-substitution
failure §3 exists to prevent.

**The `linux/amd64`-only matrix is legible, not silent.** Rust does not
cross-compile for free the way Go does: every triple needs its own std plus a
linker, and xdrtop is *structurally* blocked on arm64 because `openssl-sys`
needs an aarch64 OpenSSL and the workaround (the openssl crate's `vendored`
feature) would mean editing a submodule. `MANIFEST.json` carries
`unsupported_targets[]` with that sentence, and a request for an unbuilt target
returns **409 `TOOL_TARGET_UNSUPPORTED` quoting it verbatim** — the same class
of refusal as the push generator's `BUNDLE_TARGET_UNSATISFIABLE`, and
deliberately a *different* code from **404 `TOOL_BINARY_UNAVAILABLE`**, which
means "this target IS built, this deployment's shelf is empty" and carries a fix
that works.

**Boundary, unchanged:** bundles still do not fetch. No scenario references
these three tools in `external_tools[]`, `push_generator.py` is untouched, and
its byte-frozen goldens do not move.

---

## 5 · Resolution, composition and the rename

### `POST /api/shelf/compose`

```jsonc
{
  "scenario_id": "SIM-EDR-999",
  "adapter_refs": ["TOOL-LINPEAS"],          // optional, additive
  "stage_as": {"TOOL-LINPEAS": "/tmp/.cache/sysinfo.sh"},
  "consumer": "beacon",                       // beacon | k8s | console
  "server": "https://simcore.dc.lan:8888"     // optional
}
```

```jsonc
{
  "composition_id": "cmp_7d41a9c0f1e2b3a4",
  "scenario_id": "SIM-EDR-999",
  "consumer": "beacon",
  "server_url": "https://simcore.dc.lan:8888",
  "artifacts": [{
    "name": "linpeas.sh",                                        // THE SHELF KEY
    "sha256": "0ea7e9…",                                         // recomputed FROM BYTES
    "size_bytes": 1106683,
    "url": "https://simcore.dc.lan:8888/api/shelf/payload/linpeas.sh",
    "dest_path": "/tmp/.cache/sysinfo.sh",
    "dest_basename": "sysinfo.sh",
    "mode": "0755", "kind": "file", "renamed": true,
    "adapter_id": "TOOL-LINPEAS", "origin": "adapter_ref",
    "source_url": "https://github.com/…", "license": "MIT", "pinned": true
  }],
  "unstaged_adapters": [],
  "air_gapped": true,
  "renamed_count": 1,
  "warnings": [{"code": "FILENAME_KEYED_DETECTIONS_SUPPRESSED", …}],
  "shelf_dir": "/app/payloads",
  "staged": ["deepce.sh", "linpeas.sh"]
}
```

Rules the three consumer tracks may rely on:

* **Every artifact carries a non-empty `sha256`, or the whole call 409s.** No
  partial plan, no blank digest — a blank digest makes a consumer's
  `[ "$ACT" = "$WANT" ]` compare against `""` and pass on anything, which is how
  an integrity check silently becomes a no-op.
* `composition_id` is **deterministic**: `cmp_` + sha256 of the canonical plan
  (scenario, consumer, and each artifact's name/sha256/dest_path/mode),
  excluding `server_url` and warnings. A POV report can cite it; two DCs can
  compare.
* `air_gapped` is `false` when any referenced adapter has no artifact.
  `unstaged_adapters[]` names each one with a reason. **This is the anti-
  false-green field**: a shelf that silently covers two of a scenario's five
  tools would be the "green while proving nothing" shape.
* **Not persisted.** No DB table, no handle to reap. Stateless artifact, the same
  rule IaC bundles follow. Callers `compose()` in-process at dispatch.

`GET /api/shelf/resolve/{scenario_id}` is the same thing for one scenario with
`consumer=console` by default — a **reporting** surface whose job is to tell a
DC *before* they walk into a default-deny network.

### Destination precedence — the rename control

```
install.artifact.stage_path     (the pack — the tool's own name; == install.binary)
  ← external_tools[].stage_as   (the scenario: committed, reviewable, re-runnable)
    ← compose-request stage_as  (ad hoc)
```

**Not the pack**, because the pack is the *tool's* identity, shared by every
consumer — renaming there makes the positive case (named; the filename BIOC
fires) unauthorable. **Not the request alone**, because a rename that exists only
in an API body is not reviewable and cannot be re-run next quarter; a control
that lives in a curl flag is not a control, it is a demo.

**The shelf key never changes.** `stage_as` changes only the destination on the
target. Two scenarios staging one tool under two names fetch **one** artifact,
`MANIFEST.json` stays meaningful, and the K8s shim (which resolves by URL
basename) is untouched.

**The detection consequence, and why it must be visible.** Two corpus BIOCs key
on the literal string — e.g.
`| filter action_process_image_command_line contains "linpeas"`. Under a rename
they correctly go dark. Every renamed artifact therefore emits:

```
FILENAME_KEYED_DETECTIONS_SUPPRESSED —
  linpeas.sh is staged on disk as 'sysinfo.sh'. Detections keyed on the tool's
  FILENAME will not fire. This is a deliberate negative control: behavioural
  detections (process ancestry, enumeration burst, /proc and /etc read fan-out)
  MUST still fire. If NOTHING fires, the finding is that the customer's coverage
  is name-keyed — not that the TTP did not run.
```

Consumers must carry that warning into the run record and the POV report, or a
designed control reads as a coverage gap in front of a customer.

---

## 6 · Error codes

Standard `{"error","code","detail"}` envelope, plus per-code extras.

| HTTP | code | raised by |
|---|---|---|
| 400 | `BAD_PAYLOAD_NAME` | name fails `_NAME_RE` or is shelf bookkeeping |
| 400 | `BAD_PAYLOAD_URL` | stage: bad scheme, unresolvable host, or SSRF guard |
| 400 | `LICENSE_REQUIRED` | stage: missing / empty / `"unknown"` |
| 400 | `ADAPTER_HAS_NO_ARTIFACT` | stage/compose: `adapter_id` given, pack has no artifact |
| 400 | `PAYLOAD_DEST_REFUSED` | compose: destination outside the staging allowlist |
| 400 | `BAD_CONSUMER` | compose: notably `consumer=bundle` |
| 403 | `PAYLOAD_TOKEN_DENIED` | any gated endpoint in `token` mode |
| 404 | `PAYLOAD_UNAVAILABLE` | download / sha256 |
| 404 | `SCENARIO_NOT_FOUND` | resolve / compose |
| 404 | `ADAPTER_NOT_FOUND` | stage |
| 409 | `PAYLOAD_NOT_STAGED` | **compose** — the false-negative closer |
| 409 | `PAYLOAD_PIN_MISMATCH` | **compose** (shelf bytes ≠ pack pin) and **stage** (upstream ≠ pin) |
| 409 | `PAYLOAD_EXISTS` | stage without `replace` |
| 409 | `SHELF_EGRESS_DISABLED` | stage when `CORTEXSIM_SHELF_EGRESS=deny` |
| 413 | `PAYLOAD_TOO_LARGE` | stage over `CORTEXSIM_SHELF_MAX_BYTES` |
| 500 | `PAYLOAD_AUTH_MISCONFIGURED` | `token` mode with an empty token list |
| 502 | `PAYLOAD_FETCH_FAILED` | stage: upstream non-2xx, a 3xx, or a `text/html` body |
| 504 | `PAYLOAD_FETCH_TIMEOUT` | stage: transport failure |

Two of these are the ones consumers must never soften:

**`PAYLOAD_NOT_STAGED` (409)** names the scenario, the missing filenames, the
adapter ids, the shelf directory, what *is* staged, and `build-payloads.sh`. It
is raised **before** anything downstream exists — before a Run row, before a
manifest.

**`PAYLOAD_PIN_MISMATCH` (409)** is the check the serving endpoints
*structurally cannot* make: they recompute from bytes, so a file hand-dropped
into `CORTEXSIM_PAYLOAD_DIST` verifies perfectly against itself.

---

## 7 · Staging — `POST /api/shelf/stage`

**The egress boundary, stated in the API's own text and not only here:** this
endpoint makes an outbound HTTP request **from the SimCore process, on the DC's
host**. That is the accepted boundary and the entire point of the shelf — it
moves the public-internet dependency off the customer's cluster/endpoint (where
default-deny blocks it) onto the DC's host (where it is already accepted).
SimCore is **not** a proxy: it never fetches on a consumer's behalf at execution
time. `CORTEXSIM_SHELF_EGRESS=deny` is the air-gapped posture and returns a 409
with the offline recipe, immediately, before a socket is opened.

Preferred request form:

```jsonc
{"adapter_id": "TOOL-LINPEAS"}
```

Name, URL, digest and licence then come from the pack's `install.artifact`, so
the staged bytes are the bytes the pack was authored against. **A URL is never
parsed out of `install.runtime_install_command`** — a shell string that silently
yields the wrong URL is exactly how staging becomes a no-op.

Guarantees:

1. **Non-2xx is a failure, never a staged artifact.** A 401, a 403, a 302 to a
   captive portal and a `text/html` body all 502. This repo has shipped a
   401-counted-as-delivered bug and a 302-to-captive-portal bug in the EAL
   simulator; an HTML error page staged as `linpeas.sh` gives a perfect digest,
   verifies flawlessly on the consumer, and runs as a **no-op**.
2. **SSRF guard.** `https` only (`http` behind `CORTEXSIM_SHELF_ALLOW_HTTP=1`).
   Loopback, link-local and cloud metadata addresses are refused. RFC1918 is
   allowed — a DC legitimately stages from an internal Artifactory.
3. **`.part` → `os.replace` only after the digest is computed and any pin
   matched.** The same structural property the pod's `.part`→`mv` gives: it is
   impossible for the download endpoint to serve a torn or unverified file. There
   is no `CHECKSUM_SKIPPED` path and must never be one.
4. **No override on a pin mismatch.** Not a missing feature. A mismatch means
   upstream changed under a digest a human set; blessing that from a console is
   how an unreviewed artifact reaches a customer host, and there is no undo.
5. **It does not write `payloads/sources.json`.** That file is generated from the
   packs; an API that appended to it would fork the source of truth back into two
   lists. The response carries `pack_snippet` — the exact `install.artifact` YAML
   to paste — plus a `DECLARE_IN_PACK` warning, routing the operator to the
   durable declaration rather than around it.
6. **Synchronous.** No job table: a second stateful subsystem with its own
   lifecycle, GC and orphan story, for an operation that takes ~2 s on a 1 MB
   file. A spinner is cheaper than a queue nobody reaps. Single uvicorn worker is
   assumed (what compose ships); a multi-worker deployment needs `fcntl.flock`
   around the `MANIFEST.json` read-modify-write.

Environment:

| var | default | meaning |
|---|---|---|
| `CORTEXSIM_PAYLOAD_DIST` | `<BASE_DIR>/payloads` | shelf directory |
| `CORTEXSIM_SHELF_EGRESS` | `allow` | `deny` = air-gapped, stage 409s |
| `CORTEXSIM_SHELF_MAX_BYTES` | `67108864` | 64 MiB stage cap |
| `CORTEXSIM_SHELF_ALLOW_HTTP` | `0` | permit plain `http` for a lab mirror |
| `CORTEXSIM_K8S_PAYLOAD_AUTH` | `open` | `open` \| `token` |
| `CORTEXSIM_K8S_PAYLOAD_TOKENS` | `""` | comma-separated bearer secrets |

---

## 8 · Worked example — linpeas on the EDR plane, renamed

Goal: an EDR-plane scenario pulls linpeas from the console's shelf, an enrolled
beacon runs it as `/tmp/.cache/sysinfo.sh`, and the behavioural detections carry
the proof while the filename-keyed one correctly does not fire.

**1 · Author the pack** — `tools/packs/linpeas.yml`. **This now exists**; the
shipped pack pins release-tag `20260803-00785084` rather than the `20250601`
below, so read the file for the live values and treat this block as the shape.

```yaml
adapter_id: TOOL-LINPEAS
name: LinPEAS
version: "20250601"
tier: 4
category: cloud-container
upstream:
  # PEASS-ng is GPL-2.0-or-later (read the repo's own LICENSE; the GitHub API
  # reports NOASSERTION). This example said MIT until 2026-08-05 — a licence
  # copied from here travels into MANIFEST.json, the manifest banner and a POV
  # report a customer's legal team may read, and a staged artifact is
  # REDISTRIBUTED onto a host you do not own. Look it up, never copy it.
  license: "GPL-2.0-or-later"
  attribution: Carlos Polop (PEASS-ng)
cortex_signal:
  planes: [EDR, CDR]
  expected_techniques: ["T1082", "T1083", "T1057"]
safety_class: dual-use-lab-only
install:
  binary: /tmp/linpeas.sh
  runtime_install_command: "command -v linpeas.sh >/dev/null 2>&1 || curl -fsSLo /tmp/linpeas.sh https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh && chmod +x /tmp/linpeas.sh"
  artifact:
    filename: linpeas.sh
    url: "https://github.com/carlospolop/PEASS-ng/releases/download/20250601/linpeas.sh"
    kind: file
    sha256: "<paste what build-payloads.sh prints>"
    pin: { type: release-tag, ref: "20250601" }
    license: "GPL-2.0-or-later"
    stage_path: /tmp/linpeas.sh        # == install.binary, TA-10 back-fill
    mode: "0755"
invoke:
  target_platform: linux
  run_template: "{binary} {flags}"
  default_args:
    flags: "-q"
  identity_required: www-data
cleanup:
  commands: ["rm -f /tmp/linpeas.sh /tmp/linpeas_output.txt"]
```

Boot on this tree: `Adapter catalog loaded: 91 adapter(s) (rejected=0)`, 8 staged.

**2 · Generate the shelf declaration.**

```
$ python3 -m engine.payload_shelf --write
wrote payloads/sources.json (1 artifact(s) from 1 adapter pack(s))
```

The `TOOL-LINPEAS` entry exists now because the *pack* exists — the pre-existing
dangling `adapter_id` is closed by construction, and the `unbound[]` entry for
`linpeas.sh` is deleted in the same commit.

**3 · Stage.**

```
$ PAYLOAD_ALLOW_UNPINNED=0 ./scripts/build-payloads.sh
[payloads] fetching linpeas.sh <- https://github.com/…/download/20250601/linpeas.sh
[payloads] staged 1 artifact(s) into /home/…/payloads (0 unpinned)
$ docker build -f core/Dockerfile -t cortexsim:dev .
```

A wrong pin → `PAYLOAD_PIN_MISMATCH name=linpeas.sh expected=… got=…`, exit
non-zero. The build never blesses a changed upstream.

**4 · Author the scenario.** `SIM-EDR-022` ships the `adapter_ref` half. The two
lines below are **NOT yet authorable** — read §9 items 4 and 15 before copying:

```yaml
external_tools:
  - name: linpeas
    source: "https://github.com/carlospolop/PEASS-ng"
    type: script
    adapter_ref: TOOL-LINPEAS         # SHIPPED on SIM-EDR-022
    stage_as: /tmp/.cache/sysinfo.sh  # NOT YET — ExternalToolSchema has
                                      # extra="ignore", so this key is SILENTLY
                                      # DROPPED and the run claims a rename that
                                      # never happened. Guarded by
                                      # tests/tools/test_stage_as_tripwire.py.
steps:
  - id: step-02
    command: "{adapter:TOOL-LINPEAS}" # NOT YET in a push bundle — the
                                      # orchestrator substitutes it, but
                                      # generate_bash emits the placeholder
                                      # LITERALLY. No shipped scenario uses it.
    identity: www-data
```

**5 · Preflight.**

```
$ curl -s localhost:8888/api/shelf/resolve/SIM-EDR-022 | jq
{ "air_gapped": true,
  "artifacts": [{ "name": "linpeas.sh", "sha256": "0ea7e9…",
                  "dest_path": "/tmp/.cache/sysinfo.sh",
                  "dest_basename": "sysinfo.sh", "renamed": true, … }],
  "renamed_count": 1,
  "warnings": [{"code": "FILENAME_KEYED_DETECTIONS_SUPPRESSED", …}] }
```

**6 · Launch (pull). ⚠ THIS STEP DOES NOT HAPPEN YET.** The design is that the
orchestrator calls the same `compose()` in-process **before the Run row is
created** and embeds the plan in the queued task. It does not: `_handle_pull`
never calls `compose()`, so `Task.artifacts` is `[]` on every real launch (§9
item 5), and the emitted plan would need a projection that does not exist (§9
item 5a). Everything in step 7 below is real; only the producer is missing.

**7 · Beacon — VERIFIED (2026-08-05), with the plan injected by hand.** Fetches
from `/api/shelf/payload/{name}`, sha256s the bytes, compares against the digest
**it carried in from the task**, `.part` → `mv` only on match, `chmod 0755`,
runs the step through the identity harness as `www-data`. Observed on a target
container with **no route to github.com**:

```
[beacon] staging 3 artifact(s) for run_id=96a89e32-… before any step runs
[beacon] staged linpeas.sh   -> /tmp/linpeas.sh   sha256=0ea7e9ce5fcc…
[beacon] staged pspy64       -> /tmp/pspy64       sha256=c93f29a5cc13…
[beacon] staged suid3num.py  -> /tmp/suid3num.py  sha256=25a070b77560…
[beacon] causality-chained execution cgo="apache2" steps=5
[beacon] step step-02 complete exit_code=0        # real LinPEAS output
```

Under a rename the XDR process tree shows `www-data` executing
`/tmp/.cache/sysinfo.sh` with no `linpeas` string on any command line — but see
§9 item 15: `SIM-EDR-022`'s own steps hard-code `/tmp/linpeas.sh`, so the
renamed variant stages correctly and then fails that scenario's preflight.

**8 · The two loud failures — both verified live.**

* Nothing on the shelf → **409 `PAYLOAD_NOT_STAGED`** naming `linpeas.sh`,
  `TOOL-LINPEAS`, the shelf dir, what *is* staged, and `build-payloads.sh`.
  (The "**No Run row is created**" half depends on §9 item 5; today a pull
  launch creates the Run row first and the run fails afterwards.)
* Someone dropped a different `linpeas.sh` into `CORTEXSIM_PAYLOAD_DIST` → **409
  `PAYLOAD_PIN_MISMATCH`** with expected and actual digests, while
  `GET /payload/linpeas.sh` serves the substituted file happily — which is
  precisely the hole byte-recomputation cannot close.

---

## 9 · What is still owed, and by whom

This module owns derivation, resolution and serving. It deliberately does not
reach into files other tracks own. Each item below is a real gap, named so it
cannot be mistaken for shipped.

**Content / adapter track**

1. ~~`tools/packs/linpeas.yml` does not exist.~~ **DONE.** It exists, pinned to
   release-tag `20260803-00785084`, digest
   `0ea7e9ce5fcca464b5998cb73930e36647ebf9b38590fe250bbd5664fe8670a5`.
2. ~~`deepce.yml` needs an `install.artifact`.~~ **DONE**, commit-pinned to
   `420b1d1ddb636f6bd277a105f580cd09b03517cc`. (Its
   `runtime_install_command` deliberately stays on `/raw/main/` — it is emitted
   verbatim into the byte-frozen bundles, so repointing it moves `SIM-CDR-001`'s
   golden digest for no integrity gain.)
3. ~~The next tier-4 candidates are still owed.~~ **TRIAGED AND RECORDED
   2026-08-06 — but the count did NOT move: still 8 of 56.** Every one of the
   48 remaining packs was judged on its merits against measured evidence and
   now declares `install.artifact_exempt` (TA-13). **Zero were wired, and that
   is the correct outcome, not a shortfall** — see §11 for the finding that
   forced it. Read "**8 of 56, with 48 reasons and an 8-item backlog**", never
   "the shelf covers the tier-4 packs".

**Scenario-loader track**

4. **`external_tools[].stage_as`** in `scenarios/_schema.yml` +
   `scenario_loader.py`, with **S-19** (path outside the allowlist → ERROR,
   **not** relaxable by `CORTEXSIM_STRICT_REFS`, same posture as S-17; call
   `adapter_loader._validate_stage_path` so the three gates cannot disagree) and
   **S-20** (`stage_as` on an entry with no `adapter_ref`, or whose adapter has
   no artifact → WARNING, because the field would be silently inert and that is
   how "we proved the rename" becomes a false claim).
   `compose()` already reads `stage_as` when present.

**Orchestrator / beacon track**

5. **STILL OWED, AND BLOCKING. `_handle_pull` does not call
   `compose(consumer="beacon")`.** Verified 2026-08-05 by launching
   `SIM-EDR-022` in pull mode through `POST /api/runs` against a real enrolled
   beacon advertising `artifact-fetch`: the beacon logged
   `causality-chained execution` with **no staging phase**, because
   `Task.artifacts` was `[]`. Today the blast radius is contained only because
   `SIM-EDR-022`'s hand-authored step-01 preflight refuses — that is one
   scenario's guard, not the mechanism. Call `compose()` in the same position as
   the consent gate, **before** the Run row exists, and map
   `PayloadResolutionError` to its HTTP status.

   5a. **The projection does not exist.** `compose().to_dict()["artifacts"]`
   emits `url` (absolute) + `dest_path`. `agent/beacon/artifact.go::ArtifactSpec`
   requires `path` (server-**relative**, joined onto the beacon's own
   `ServerURL`) + `dest`. `orchestrator.Task.artifacts`'s docstring says the
   entries are what compose produces *"projected onto the beacon's wire
   contract"* — nobody wrote the projection, and nothing could have caught it
   because the only caller that would join the two halves is item 5. Feeding
   compose's output through unprojected makes the beacon refuse every artifact
   with `ARTIFACT_SPEC_INVALID: path="" must be SERVER-RELATIVE` (observed).
   Whichever track lands item 5 must land a single named projection function
   with a test that asserts the emitted keys equal the Go struct's json tags.
6. ~~`Task.artifacts`~~ **DONE** — field present, omitted when empty, rehydrated.
7. ~~A capability gate.~~ **DONE at delivery time** (`poll_tasks` → 409
   `AGENT_CANNOT_STAGE_ARTIFACTS`). The launch-time version is still owed with
   item 5, so a refused run currently leaves a `failed` Run row rather than none.
8. ~~Fail loudly.~~ **DONE and verified live** — exit 78, per-step
   `ARTIFACT NOT STAGED` frame, and the "THIS STEP DID NOT RUN … NOT a gap in
   the customer's detection coverage" vocabulary in the run record.
   **But see item 14:** that vocabulary does not reach the POV report.

**K8s track**

9. ~~`k8s_manifest._resolve_payloads` should delegate to `compose()`.~~ **DONE**,
   narrowly (literal `cluster_posture.payloads` only, never `external_tools` —
   an adapter-derived artifact would move off `/cortexsim/tools/` and break the
   shim). Generated manifests are byte-identical apart from wall-clock TTLs.
10. ~~`generate_k8s` should refuse when `delivery=served` + shelf auth is
    `token`.~~ **DONE** (`_guard_served_shelf_auth`) — **but it surfaces as a
    500 `INTERNAL_ERROR`** carrying the right text, because
    `core/api/scenarios.py` does not map `ShelfAuthUnreachableFromCluster`. A 500
    reads as "CortexSim is broken", not "your shelf-auth setting makes this
    delivery mode unusable". The 7-line 409 handler is in the exception's
    docstring.

**CI**

11. **STILL OWED.** Add `python3 -m engine.payload_shelf --check` to the existing
    `adapters` job and run `PAYLOAD_ALLOW_UNPINNED=0` wherever the shelf is
    staged. The drift gate does run in-process as a pytest; the pinned-build
    enforcement does not run anywhere in CI.

**Resolver track (found during verification, 2026-08-05)**

12. **`compose()` must not silently drop an unknown `adapter_ref`.** It
    `continue`s with a `logger.warning`. The comment justifies this as avoiding
    "a scenario outage from an unloaded catalog in a test fixture" — that trades
    the module's central safety property for test convenience. With an empty
    catalog, `SIM-EDR-022` composes to `artifacts: []`,
    `unstaged_adapters: []`, `air_gapped: true`, `warnings: []` and a citable
    `composition_id`. That is the exact "green while proving nothing" shape, and
    this repo has already shipped the enabling bug once (the prod image not
    `COPY`ing the UC/TC snapshot, which turned strict validation into a no-op).
    Minimum fix: the ref lands in `unstaged_adapters[]` with reason
    `ADAPTER_NOT_IN_CATALOG`, and `air_gapped` goes `false`.
13. **`core/api/payloads.py::list_declared_artifacts` calls
    `declared_artifacts()` with no `scenarios=`**, so every artifact reports
    `used_by = "(no scenario references this adapter yet)"`. The generated
    `payloads/sources.json` — same function, called by the CLI *with*
    `scenarios=` — correctly says `linpeas.sh → SIM-EDR-022`. Two callers of one
    function disagree; the console reads the API, so it tells a DC that nothing
    will fetch `TOOL-LINPEAS` during a run, for the scenario built to prove
    exactly that, and falls back to "preview against any scenario … a preflight,
    not coverage".

**Report track**

14. **`core/api/runs.py::_build_markdown_report` has no execution-integrity
    section.** A staging failure renders as an ordinary failed run: every seeded
    Result ❌, 0 % coverage, no explanation — the manufactured false negative
    relocated from the endpoint into the customer-facing document, which is the
    artifact a DC actually shows. The marker is live and stable in `run.output`:
    `ARTIFACT STAGING FAILED:` (emitted identically by the beacon and by the
    `agents.py` capability gate).

**Content track**

15. **`SIM-EDR-022`'s steps hard-code `/tmp/linpeas.sh`,** so composing with a
    `stage_as` rename stages the artifact at the new path and then fails
    step-01's own preflight (observed: staging OK, step-01 exit 1). The rename
    control is therefore expressible in `compose()` but **not runnable against
    the one scenario built for it** until item 4 (`stage_as` at load) and the
    `{adapter:TOOL-X}` placeholder in `push_generator` both land. Step-04 also
    assumes `python3` on the target with no preflight — on a host without it the
    run dies at exit 127 with no CortexSim framing, which reads as a TTP failure.

---

## 10 · Files

```
core/tools/adapter_loader.py     ArtifactSchema/ArtifactPinSchema, TA-01..TA-12,
                                 _validate_stage_path (the one path gate),
                                 .staged / .effective_stage_path
core/tools/adapter_catalog.py    TA-12 enforced on the runtime singleton too
core/engine/payload_shelf.py     derivation · sources.json generation + drift ·
                                 compose() · the error family · the CLI
core/api/payloads.py             /api/shelf/* and /api/k8s/* from one handler set;
                                 /artifacts /compose /resolve /stage; boot WARNs
core/main.py                     the second include_router
payloads/sources.json            regenerated: $generated · payloads[] · unbound[]
scripts/build-payloads.sh        stages payloads[] + unbound[]; refuses kind != file
tools/packs/_schema.yml          the annotated install.artifact reference
tests/tools/test_adapter_artifact_schema.py
tests/engine/test_payload_shelf.py
tests/api/test_payloads_shelf.py

core/Dockerfile                  rust-builder stage → /app/rust-dist (the Rust tool shelf)
core/api/tools_dist.py           GET /api/tools/binar{ies,y/{tool}[/sha256]}
tests/tools/test_rust_dist_stage.py              recipe + bake guards
tests/tools/test_rust_dist_route_registration.py the router-is-mounted guard
```

**Explicitly NOT touched:** `core/engine/push_generator.py` (byte-frozen),
`tests/engine/_golden/push_bundle_digests.json`,
`k8s_manifest._SERVED_FETCH` (proven on a live kind cluster).

---

## 11 · Tier-4 triage (2026-08-06) — why the count did not move

**Headline: 8 of 56 tier-4 packs are shelf-backed. 48 are exempt with a stated
reason. 0 are undeclared. Zero packs were wired in this pass, deliberately.**

Every one of the 48 was judged individually against evidence obtained by
*querying upstream and downloading the artifact*, not by reading the install
command. The verdicts live in the packs (`install.artifact_exempt`), enforced
by TA-13..TA-17, so they cannot rot silently and cannot be skipped by the next
author.

### 11.1 · The finding that stopped the wiring

The triage brief expected roughly five "release archive" packs to be
convertible. The measured result is sharper, and it inverts the plan:

> **Not one of the 48 remaining packs is a bare single file at a pinned URL.**
> Every candidate that publishes releases at all ships `.tar.gz` / `.zip` /
> `.deb` / `.rpm`. The one exception, `kubescape`, is a **262,032,010-byte**
> bare binary — 249.9 MiB against a 64 MiB cap.

So `kind: archive` is not a corner case, it is the *entire* remaining
opportunity — and it is rejected at load (TA-08) because **no consumer can
unpack one**. Re-verified on this tree, because the old comment in
`_schema.yml` claimed the opposite and would have made wiring look cheap:

| consumer | evidence | verdict |
|---|---|---|
| Go beacon | `agent/beacon/artifact.go::Artifact` has `Name`/`SHA256`/`SizeBytes`/`Path`/`Dest`/`Mode`/`Steps` — **no `Kind` field at all**. A `grep` for `archive/tar`, `archive/zip` and `compress/gzip` across `agent/` returns nothing. | cannot see `kind`, let alone branch on it |
| K8s init container | `k8s_manifest._SERVED_FETCH` is `wget` → `sha256sum` → `chmod` → `mv`, no extraction branch; alpine busybox has `tar -z` but **no `unzip`** | cannot extract |
| `scripts/build-payloads.sh` | hard-fails on `kind != file` | cannot stage |

Wiring an archive-shaped artifact today would produce **exactly the failure
this repo keeps re-shipping**: `compose()` would emit an artifact the beacon
refuses with `ARTIFACT_SPEC_INVALID`, or worse, stage bytes no consumer can use
while the console reported `air_gapped: true`. A pack whose artifact cannot be
consumed is worse than one that honestly still uses `runtime_install_command`.

**The unlock, when someone owns it, is single-member extraction on the DC's own
SimCore** — download the pinned archive, verify the *archive* digest, extract
one named member, verify the *member* digest, put that one file on the shelf.
`compose()`'s wire output stays byte-identical (`kind: "file"`) and neither
stdlib-constrained consumer learns a new word. That work spans
`core/engine/payload_shelf.py` and `core/api/payloads.py`, **outside this
pass's file scope**, which is the second reason nothing was wired.

### 11.2 · The verdicts

| reason_code | n | meaning | backlog? |
|---|--:|---|:--:|
| `DISTRO_PACKAGE` | 18 | apt/yum. Shelving = hosting a Debian mirror. | — |
| `LANGUAGE_PACKAGE_MANAGER` | 12 | pip/gem + dependency trees. | — |
| `SOURCE_TREE_REQUIRED` | 7 | the tool **is** a directory, not a file. | — |
| `ARCHIVE_ONLY_NO_EXTRACTOR` | 6 | upstream ships only an archive. | **yes** |
| `NEEDS_RUNTIME_DATA` | 2 | binary is inert without a feed it fetches at run time. | — |
| `LICENCE_NO_REDISTRIBUTION` | 1 | no grant, so no right to serve it onto a customer host. | — |
| `ARCHIVE_MEMBER_NOT_SELF_CONTAINED` | 1 | the binary alone does not run. | **yes** |
| `ARTIFACT_TOO_LARGE` | 1 | over `CORTEXSIM_SHELF_MAX_BYTES`. | **yes** |
| | **48** | | **8** |

The backlog is **8 adapters**, each carrying a mandatory `revisit` line (TA-17)
with the measured next action. Everything else is settled: **39 of the 48 will
never be shelvable without hosting a mirror of someone else's package index.**

Judgements worth reading rather than re-litigating:

* **The two scanner packs that fetch a data feed are `NEEDS_RUNTIME_DATA`, not
  "too big".** One clones its template repo on first run; the other pulls its
  vulnerability DB every scan. Staging only the binary would remove the
  *install* download and leave the *data* download — so the target would still
  need egress while CortexSim reported the scenario as `air_gapped: true`. That
  is a false-green the shelf would have manufactured itself, which is why size
  is the lesser reason.
* **`kube-bench` is the honest boundary of the one-file contract.** It is
  *under* the size cap and looks shelvable on the release page. Opening
  `kube-bench_0.10.3_linux_amd64.tar.gz` (sha256 `f4fc4d51…`) shows **205
  entries**: the binary plus a `cfg/` CIS-benchmark tree. The binary alone
  exits with a config error. Only opening the archive shows this.
* **`credking` is `LICENCE_NO_REDISTRIBUTION`, and that outranks its other
  disqualifiers.** Verified 2026-08-06: no `LICENSE` file, and GitHub reports
  no licence. The shelf does not *cache* a tool, it **redistributes** it — the
  bytes go into the image and out of `/api/shelf/payload/{name}` onto a
  customer's endpoint, recorded in a `MANIFEST.json` a legal team may read.
  The pack's `upstream.license: LicenseRef-ustayready` names a grant that does
  not exist and should be corrected regardless of shelf work.
* **`seclists` has no defensible subset.** The single corpus reference
  (`mp-019`) reads `-w /usr/share/seclists/...`, the **Kali package path**, not
  the adapter's clone path — and no scenario carries `adapter_ref:
  TOOL-SECLISTS`. Shelving one `common.txt` would ship a 3.3 GB corpus's name
  attached to 0.03 % of its content.
* **`evil-winrm` was judged individually, and the gem is not the deciding
  factor.** rubygems lists **8 runtime dependencies** (`winrm`, `winrm-fs`,
  `logger`, `csv`, `stringio`, `syslog`, `fileutils`, `benchmark`) and the
  GitHub release publishes no assets. There is no single file that runs.

### 11.3 · Two dead upstream URLs, found by probing every pinned URL

Neither is visible to any current gate, and both produce the manufactured false
negative the shelf exists to prevent — the step runs with no tool, detects
nothing, and the absent detection reads in a POV report as *"Cortex missed
it"*.

1. **`tools/packs/trivy.yml`** — `runtime_install_command` fetches the
   `v0.55.2` release tarball. Measured: **`HTTP/2 404`**. Aqua prunes old
   releases (v0.55.0 also 404s; v0.73.0 is live). `SIM-CDR-007`'s trivy step
   therefore cannot install its tool on **any** target, with or without egress.
   **NOT fixed in this pass, on purpose:** `runtime_install_command` is emitted
   *verbatim* into the byte-frozen push bundles, so repointing it moves
   `SIM-CDR-007`'s entry in `tests/engine/_golden/push_bundle_digests.json` —
   a file outside this pass's scope. It is a legitimate golden update (the
   frozen bytes currently freeze a broken URL) and belongs in a commit that can
   regenerate it. Noted in the pack's exemption.
2. **`tools/packs/bloodhound.yml`** (tier 3) — `content_library_entry.
   download_url` points at the `v5.13.0` linux zip. Measured: **`HTTP/2 404`**.
   BloodHound has moved to a v9 line that publishes **no release assets at
   all**, so there is no drop-in replacement URL — the ITDR IaC module's
   BloodHound install needs a new install strategy, not a version bump. Out of
   this pass's scope to fix.

The detector shipped with this pass:
`tests/tools/test_tier4_artifact_declaration.py::test_every_pinned_release_url_still_resolves`
HEADs every `/releases/download/` URL in the pack tree. It is **opt-in**
(`CORTEXSIM_CHECK_UPSTREAM_URLS=1`), because a network failure in a unit-test
job is indistinguishable from upstream rot and a flaky gate gets ignored —
which is how the rot survives. Run it on a schedule, not per-PR.

### 11.4 · Still owed by other tracks (this pass could not reach them)

* **`payload_shelf.required_artifacts` should read the exemption.**
  `compose().unstaged_adapters[]` still emits one generic sentence for all 48.
  The pack data now exists — read `adapter.install.artifact_exempt` /
  `adapter.artifact_exempt_reason_code` and surface `{adapter_id, reason_code,
  reason, revisit}`. Until then the *durable declaration* is right and the
  *console string* is still the old one.
* **`GET /api/shelf/artifacts` should grow `exempt[]` + a `summary` block** so
  the console can render "48 of 56 runtime-fetched tools are not staged, each
  names why" without client-side counting. Style `ARCHIVE_*` /
  `ARTIFACT_TOO_LARGE` rows as backlog and the rest as settled — **no red**: an
  exemption is a correct state, and colouring 48 rows red trains a DC to ignore
  the panel.
* **`payloads/sources.json` cannot carry `exempt[]` yet** — the renderer lives
  in `payload_shelf.py`. When added it must fall under the same
  byte-determinism and `--check` drift rules, and `build-payloads.sh` must
  **ignore** it: it is documentation, not a staging instruction.
* **The beacon does not prepend the stage directory to `PATH`.** Every corpus
  step invokes these tools by bare name, the staging allowlist correctly
  forbids `/usr/local/bin`, and a `grep` for `PATH` across `agent/beacon/` and
  `agent/executor/` finds only test files. **This is a hard dependency of the
  archive-member work, not a follow-up:** without it those tools would stage
  perfectly and every step would still fail `command not found`.
