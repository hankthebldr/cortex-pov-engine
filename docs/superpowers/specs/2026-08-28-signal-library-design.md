# The Signal Library — design

Status: **design** (awaiting review) · 2026-08-28
Supersedes the framing in: `docs/reference/payload-shelf.md` §0 "Why the shelf exists at all"
Companion to: `docs/design/e2e-execution-methodology.md` (Tier C), `docs/tool-adapters.md`

---

## 0 · The correction this design exists to make

`docs/reference/payload-shelf.md` justifies the shelf as a **delivery
workaround**:

> All 50 tier-4 packs install their tool from the public internet, on the target
> host, at dispatch. The customers who buy Cortex run default-deny egress, so
> that is the first thing their network blocks.

True, and still true. But it is the *constraint*, not the *purpose*. The purpose
is:

> **Cortex XDR detects by real-time process monitoring. A detection only exists
> if a real program actually executes on the target endpoint. The shelf exists
> to broaden the diversity of tools, samples and signatures producing that
> execution.**

Everything below follows from that sentence, and it changes the sizing of the
system by three orders of magnitude. A delivery workaround needs 8 artifacts. A
signal library needs every publicly available malware sample, exploit tool and
offensive binary we can lawfully obtain.

The shelf as built cannot hold that:

| | Shelf as built | Signal library |
|---|---|---|
| Keyed by | adapter pack, **one artifact each** (`install.artifact`) | technique → **many** artifacts |
| Inventory | 8 staged · 48 exempt · 0 unbound | thousands |
| `kind: archive` | **rejected** (`TA-08`) | mandatory |
| Pinning | 8 hand-authored `sha256` | ingested from source |
| Curation | a human edits YAML per tool | automated ingestion |
| Validated by | nothing — bytes are served unexecuted | Tier-C detonation before POV-eligible |

## 1 · Boundaries fixed up front

| boundary | why |
|---|---|
| **The target still never reaches the public internet.** Artifacts flow `upstream → central store → the DC's SimCore → beacon/pod`. | The beacon refuses absolute URLs on purpose (`agent/beacon/artifact.go:424`) — an absolute URL in task data would let task data redirect the fetch off the operator-fixed origin. Broadening the corpus must not broaden the target's egress. |
| **`compose()` stays a pure resolver.** Auto-staging is orchestrator policy, never resolver behaviour. | `compose()` has four callers; two of them (`/api/shelf/compose` console preview, `k8s_manifest`) must never trigger a download. Browsing the UI must not fetch malware. |
| **The digest is always carried in, never fetched from the server being trusted.** | Unchanged from the existing integrity model. Scale does not relax it. |
| **Nothing is POV-eligible until it has detonated in Tier C.** | A corpus of thousands cannot be hand-verified. An artifact that does not execute produces no detection, and an absent detection reads in a POV report as "Cortex missed it" — the exact false negative the shelf exists to prevent, now at scale. |
| **Uniform consent.** One `simulation_authorized` gate covers the whole corpus. | Operator decision, 2026-08-28. See §7 — the trade-off is recorded there, not re-litigated here. |
| **`payloads/sources.json` stays generated, never hand-edited.** | Unchanged. The generator's input widens from adapter packs to the catalog. |

## 2 · Architecture

```
┌─ INGEST ────────────────────────────────────────────────────────────┐
│  source adapters: MalwareBazaar · Atomic Red Team (submodule,       │
│  already in tree) · ExploitDB · LOLBAS/GTFOBins · vendor PoCs       │
│         │  emits catalog entries + bytes, digest-keyed              │
└─────────┼───────────────────────────────────────────────────────────┘
          v
┌─ CATALOG ───────────────────────────────────────────────────────────┐
│  content/artifacts/*.yml — technique -> MANY artifacts.             │
│  Independent of tools/packs/. An adapter pack MAY reference a        │
│  catalog entry; it no longer owns it.                                │
└─────────┼───────────────────────────────────────────────────────────┘
          v
┌─ QA GATE (Tier C) ──────────────────────────────────────────────────┐
│  deploy/tier-c/ — hermetic, internal:true, auditd.                  │
│  Detonate -> observed signature (execve tree, setuid, connect,      │
│  cred access). No signature -> quarantined, never POV-eligible.     │
└─────────┼───────────────────────────────────────────────────────────┘
          v
┌─ CENTRAL STORE ─────────────────────────────────────────────────────┐
│  full corpus at rest (S3/Artifactory). DCs never mirror it whole.   │
└─────────┼───────────────────────────────────────────────────────────┘
          v
┌─ DC SimCore SHELF ──────────────────────────────────────────────────┐
│  lazily holds only what its launched scenarios needed (§6)          │
└─────────┼───────────────────────────────────────────────────────────┘
          v
      beacon / K8s pod  ->  EXECUTES ON TARGET  ->  Cortex sees it
```

Only the catalog and the ingestion pipeline are wholly new. Tier C is shipped.
The fetch/verify/serve path exists and is reused unchanged.

## 3 · The artifact catalog

New: `content/artifacts/<family>/<id>.yml`. Deliberately **not** under
`tools/packs/` — a pack describes *a tool the engine can invoke*; a catalog
entry describes *bytes that can be executed on a target*. Conflating them is
what caps the shelf at one artifact per pack.

```yaml
id: ART-2026-004112                 # stable, catalog-assigned
filename: pspy64
kind: file                          # file | archive
sha256: "3d6f…"                     # primary key at the source for most feeds
size_bytes: 3078711
source:
  feed: github-release              # matches an ingestion adapter
  url: "https://github.com/DominicBreuker/pspy/releases/download/v1.2.1/pspy64"
  pin: { type: release-tag, ref: "v1.2.1" }
license: GPL-3.0                    # required; travels into POV reports
classification:                     # METADATA ONLY — not a gate (see §7)
  family: process-monitor
  disposition: benign-tool          # benign-tool | dual-use | live-malware
technique:
  mitre: [T1057]
  behaviours: [process-enumeration]
delivery:
  stage_path: /tmp/pspy64
  mode: "0755"
signature:                          # WRITTEN BY THE TIER-C GATE, not by hand
  status: verified                  # unverified | verified | quarantined
  observed_at: "2026-08-28T14:02:11Z"
  execve: ["/tmp/pspy64"]
  setuid: []
  connect: []
  cred_access: []
```

`signature` is machine-written (§5) and is the field that makes a large corpus
trustworthy: it records what the artifact *actually did*, not what someone
claimed it would do.

For `kind: archive`, `delivery` additionally carries:

```yaml
delivery:
  archive: { format: zip, password: "infected", member: "sample.exe" }
  stage_path: /tmp/sample.exe
```

`password: infected` is the near-universal convention for public malware
distribution; without archive support the majority of the corpus is
unreachable.

## 4 · Archive delivery — retiring `TA-08`

`TA-08` rejects `kind: archive` today, and the shelf doc calls that
*"the whole remaining opportunity"*. Under this design it is the single
blocking defect, because essentially every public malware corpus distributes
password-protected ZIPs. Three consumers need work, and all three must land
together or the rejection stays correct:

| consumer | today | needed |
|---|---|---|
| beacon (`agent/beacon/artifact.go`) | `Artifact` struct has **no `Kind` field** | `Kind`, `Archive{Format,Password,Member}`; extract after digest verification, before `stage_path` placement. Go stdlib `archive/zip` handles it; ZipCrypto passwords need a small helper. |
| K8s init container | `wget`+`mv` on an image without `unzip` | add busybox `unzip` or perform extraction in the same Go helper |
| `scripts/build-payloads.sh` | hard-fails on `archive` | extract-and-verify-member |

**Digest discipline is unchanged and non-negotiable:** the pin is verified
against the *downloaded archive*, and the extracted member's own digest is
recorded separately. Verifying only the member would let a re-packed archive
pass; verifying only the archive would let a swapped member through.

## 5 · The Tier-C detonation gate

`deploy/tier-c/` already provides everything required: Ubuntu 22.04 with the
identity-harness service accounts, auditd keyed on
`cortexsim_exec/setid/net/creds`, a DNS+HTTP sinkhole, an `internal: true`
bridge with no gateway, and `run-tier-c.sh`.

New: `scripts/detonate-artifact.sh <artifact-id>` — stage the artifact into a
Tier-C runner, execute it, harvest the audit log, and write the `signature`
block back to the catalog entry.

Promotion rule:

| detonation result | catalog `signature.status` | POV-eligible |
|---|---|---|
| executes, emits ≥1 audited event | `verified` | yes |
| executes, emits nothing | `quarantined` | **no** — it cannot produce a detection |
| fails to execute | `quarantined` | **no** |
| never detonated | `unverified` | **no** |

`compose()` refuses any artifact whose status is not `verified`, with a new
`ARTIFACT_UNVERIFIED` code. This is the mechanism that lets the corpus grow to
thousands without the count becoming a lie — *authored is not proven* applies
to artifacts exactly as it applies to test cases.

**This revisits a stated non-goal.** `e2e-execution-methodology.md` says
*"Sandboxing real C2 frameworks (sliver, havoc) — those are tier D territory.
Tier C uses behavioral stubs."* That boundary was drawn when Tier C's job was
regression-testing scenario scripts. Detonating real adversary binaries in the
hermetic runner is a **new** use of the same infrastructure, and the isolation
guarantees it already proves (no gateway, no published ports, ephemeral,
audited) are exactly the ones this needs. The non-goal must be amended rather
than silently violated.

## 6 · Lazy fetch on the DC's SimCore

Approved 2026-08-24. Unchanged by the scale-up; the central store simply
becomes the upstream.

- **Origin:** SimCore only. The target never fetches from the internet.
- **Trigger:** lazily, at scenario launch. Only what is used is fetched.
- **Blocking:** the launch waits; the event loop does not. `stage_payload` is
  already `async` on `httpx.AsyncClient`, so `_compose_artifacts` becomes
  `async def` and awaits it — no `asyncio.to_thread` required.
- **Placement:** policy lives in the orchestrator, not in `compose()`.

```
launch()  ──> await _compose_artifacts(scenario)
                 ├─ compose()  ──ok──────────────────────> artifacts
                 └─ PayloadNotStaged
                      ├─ autostage off / egress denied ─> re-raise unchanged
                      └─ await autostage(ids)
                           ├─ ok   ─> compose() again ──> artifacts
                           └─ fail ─> raise, enriched with per-artifact codes
```

Supporting changes:

- **Extraction:** move the fetch core out of `api/payloads.py::stage_payload`
  into `core/engine/shelf_staging.py::fetch_to_shelf()`, raising a structured
  `ShelfStagingError` rather than `HTTPException`. The endpoint becomes a thin
  wrapper; the orchestrator gains a caller. Behaviour of the endpoint is
  unchanged.
- **Concurrency:** the `.part` path is shared per name. A module-level
  `dict[str, asyncio.Lock]`; the second waiter re-checks `dest.exists()` and
  takes the cache hit.
- **Persistence:** `docker-compose.yml` gains `- ./payloads:/app/payloads`.
  Without it, everything staged is lost on `--force-recreate` — today's silent
  re-degradation. `payloads/.gitignore` is already `*` + allowlist.
- **Config:** `CORTEXSIM_SHELF_AUTOSTAGE`, default **on**, subordinate to
  `CORTEXSIM_SHELF_EGRESS`. Default-off would mean the feature does nothing out
  of the box; air-gapped DCs already set `CORTEXSIM_SHELF_EGRESS=deny` and keep
  the existing refusal path verbatim.

## 7 · Consent — uniform, by operator decision

One `consent.simulation_authorized` gate covers the entire corpus. A live
ransomware sample and `linpeas.sh` are launched by the same affordance.

Recorded honestly, because a spec that hides a trade-off is worthless: the
alternative offered was a per-artifact `authorization_tier` requiring an ROE
reference for the `live-malware` disposition. The operator chose uniform on
2026-08-28 after that trade-off was stated. This design implements uniform.

`classification.disposition` is still **recorded** on every entry — not as a
gate, but because MITRE mapping, POV reporting and audit logging all need to
state what was executed on a customer endpoint. Metadata that describes is not
metadata that blocks.

## 8 · Storage — central store, lazy subset

Full corpus at rest in an internal artifact store. Each SimCore holds only what
its launched scenarios required. Consequences:

- No DC host mirrors a multi-gigabyte malware corpus.
- The central store is the ingestion and detonation target; DCs consume
  already-`verified` artifacts.
- Air-gapped posture is unchanged: `CORTEXSIM_SHELF_EGRESS=deny` plus a
  pre-populated `CORTEXSIM_PAYLOAD_DIST`.

**Open — deferred deliberately:** the store's identity/authn shape (bucket
policy vs. Artifactory token) is not specified here. It depends on where PANW
wants the corpus to live, which is an infrastructure decision outside this
repo.

## 9 · Operational constraints — real, and not designed away

1. **The DC's own endpoint will fight this.** A shelf of live samples on a DC
   laptop gets quarantined by whatever EDR runs there — plausibly Cortex. The
   central store, not the laptop, is where the corpus lives; the DC's lazily
   fetched subset still needs a documented exclusion path.
2. **Source APIs need credentials.** MalwareBazaar requires an API key. Each
   feed has its own terms and shapes; ingestion adapters carry that per-feed.
3. **Two upstream URLs in the current 8 are already dead** (`payload-shelf.md`
   §11.3, found by probing every pinned URL). At corpus scale, link-rot is
   continuous, not incidental — ingestion must re-probe and mark entries stale.

## 10 · Deferred to backlog

Per operator direction 2026-08-28, focus is detection and execution. Anything
requiring an external Cortex tenant is out of scope for this work and is
**already default-off** at runtime:

| item | state |
|---|---|
| `CORTEXSIM_AUTO_RECONCILE` | default off — unchanged |
| `CORTEXSIM_AUTO_VERIFY` (Tier-2 XQL) | default off — unchanged |
| Connector preflight, Readiness ladder | dormant, no outbound calls at boot |
| Assertion XQL probes (`xql_*`) | require a tenant; not exercised here |

`/api/health` already makes zero outbound calls, so no code change is required
to satisfy this. It is recorded so the next reader does not re-open it.

## 11 · Testing

The injectable `stage_client_factory` seam means none of this touches the
network in tests.

- miss → stage → compose succeeds
- pin mismatch refuses; HTML body refuses; egress-denied refuses with no socket
- archive: wrong password, wrong member, correct member — digest checked at
  both archive and member level
- concurrent double-launch stages exactly once
- staged bytes survive container recreate (the volume)
- `signature.status != verified` refuses with `ARTIFACT_UNVERIFIED`
- **regression guard, protecting §1:** `/api/shelf/compose` and `k8s_manifest`
  never stage — assert the injected transport was never called from either path

## 12 · Implementation phases

This design is deliberately larger than one implementation plan. It decomposes
into six increments, each independently shippable and independently valuable —
no phase is a prerequisite for the one before it being useful.

| # | Scope | Value on its own | New? |
|---|---|---|---|
| **1** | **Fetch mechanics.** Extract `fetch_to_shelf()`; `_compose_artifacts` async + autostage policy in the orchestrator; per-name lock; `./payloads` volume; `CORTEXSIM_SHELF_AUTOSTAGE`; the preview/manifest never-stage guard. | The shelf stops silently re-degrading after every `--force-recreate`, and launches self-heal instead of refusing. Works against today's 8 artifacts with no catalog. | approved 2026-08-24 |
| **2** | **Archive delivery.** `Kind`/`Archive` on the beacon struct, extraction after digest verification, K8s init extraction, `build-payloads.sh`. Dual digest discipline (archive *and* member). | Retires `TA-08` and unlocks the majority of public corpora. Testable end-to-end with a single known password-protected ZIP. | yes |
| **3** | **Artifact catalog.** `content/artifacts/*.yml` + loader + validation codes; packs may *reference* a catalog id instead of owning an artifact; `sources.json` generator input widens. | "Many artifacts per technique" becomes expressible at all. | yes |
| **4** | **Detonation gate.** `scripts/detonate-artifact.sh`, the `signature` block, `ARTIFACT_UNVERIFIED` in `compose()`, and the amendment to Tier C's non-goal. | Corpus growth stops being an unverified claim — the count means something. | yes |
| **5** | **Ingestion adapters.** Atomic Red Team **first** — it is already a submodule, needs no credentials, and is benign, so it proves the pipeline end-to-end cheaply. Then MalwareBazaar (API key), ExploitDB, LOLBAS/GTFOBins. | The corpus scales without hand-authoring. | yes |
| **6** | **Central store.** Store backend, DC-side pull, staleness re-probing for link-rot. | DCs stop mirroring the corpus; §9.3 link-rot becomes managed rather than discovered mid-POV. | yes |

Ordering rationale: phases 1 and 2 are pure mechanics and unblock everything
else; 3 must precede 4 and 5 because both write to catalog entries; 6 is last
because phases 1–5 all work against a local shelf and only 6 changes where the
bytes live at rest.

**Phase 5 starting with Atomic Red Team is load-bearing, not incidental.**
Proving an ingestion pipeline against a benign, credential-free, already-vendored
corpus means the first live-malware ingestion is exercising *one* new variable
instead of five.

Each phase gets its own implementation plan.

## 13 · Non-goals

- Replacing Tier D. Tier C proves the artifact executes and what it does; only
  a real tenant proves a detection fired. `tenant-verified` stays 0 until a
  tenant answers.
- Authoring detections for every artifact. The corpus supplies signal; mapping
  signal to `expected_detections` remains scenario-authoring work.
- Hosting a Debian mirror. The 18 `apt`-family `artifact_exempt` packs stay
  exempt; this design does not close them and does not pretend to.
