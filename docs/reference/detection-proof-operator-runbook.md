# Detection Proof Layer — Operator Runbook

> **Audience:** the DC driving a live Cortex (XDR / XSIAM / Cortex Cloud) POV.
> This is the end-to-end journey for the new Detection Proof Layer: enroll an
> agent → launch a scenario → open the Detection Storyline presenter → watch
> Cortex detections light up → capture evidence → export the CISO scorecard.
>
> **As-of:** 2026-07-10 · branch `ultracode/full-revamp`. Grounds in the
> Detection Proof Layer modules (`core/engine/storyline.py`,
> `core/engine/scorecard.py`, `core/models_evidence.py`,
> `core/connectors/{matcher,evidence_capture,deep_link}.py`,
> `core/api/{live_frames,sse_backfill}.py`, `ui/src/components/console/*`).

## What the layer adds (mental model)

Every run now has **one spine and two faces.** The spine is a per-run
**Detection Storyline** — an ordered kill chain where each entry is
`step → expected detection → observed Cortex alert → real MTTD → persisted
evidence`. The *same* structure drives both faces:

- **The presenter timeline** (live, on-stage): a chip per expected detection
  that flips `pending → detected` the instant a real alert reconciles, badged
  with its Cortex engine (`BIOC | XQL | Analytics | Correlation | IOC | ABIOC`)
  and true MTTD.
- **The executive scorecard** (the CISO one-pager): per-Cortex-product coverage,
  MTTD p50/p90, detection-type mix, ranked gaps, and a machine-verified-vs-
  attested provenance split.

The old flow lost the proof at three chokepoints — a human clicked a checkbox,
the matched alert was flattened into a notes string, and the live frame dropped
the detection id. The Proof Layer closes all three: evidence is **persisted**
(`ResultEvidence`), the live frame is **enriched**, and both faces read the same
evidence-backed rows.

---

## Prerequisites (one-time, per POV)

1. **SimCore up.** `./scripts/dev-up.sh` (or `docker compose up -d --build`);
   console at `http://localhost:8888/`.
2. **Read-only XSIAM integration credential** stored in the encrypted vault:
   `POST /api/credentials/integrations` with the tenant FQDN + API key id/secret.
   This is what powers automatic reconcile and the tenant deep-links
   (`core/connectors/xsiam.py` reads the FQDN; `deep_link.build_console_link`
   derives `https://<fqdn>/alerts/<id>` from it). **No credential? The layer
   still works offline** — you paste observed alerts by hand (Step 6b) and every
   evidence row is badged `source='manual'` / *attested* instead of *machine-
   verified*.
3. **Confirm reachability** on the pre-flight strip in the In-flight view:
   agent `online` + connector reachable (`GET /api/connectors`). Green on both
   before you present.

---

## Step 1 — Enroll the agent (the front door)

Use the **enrollment-token** flow, not the legacy self-asserted `--id`.

```bash
# On SimCore: mint a short-TTL, revocable token.
curl -s -X POST http://localhost:8888/api/agents/enroll/tokens \
  -H 'Content-Type: application/json' \
  -d '{"ttl_seconds": 900, "max_uses": 1, "label": "acme-jumpbox"}'
# → { "token": "…", "expires_at": "…" }

# On the customer jumpbox: one line, no id to invent.
curl -s 'http://<simcore>:8888/api/agents/install?token=<TOKEN>' | bash
```

SimCore *assigns* the agent id via `POST /api/agents/enroll`. Liveness shows as
`online / stale / offline` (derived from `last_seen`, with a heartbeat sweep
emitting `agent.status` SSE frames). Wait for **online** before launching.

## Step 2 — Launch the scenario

Pick a plane → scenario → **Launch**. On launch the orchestrator:

- creates the `Run`, then **auto-seeds one `Result` row per expected detection
  per step** — this is your ground-truth denominator (coverage is measured
  against a *known* set, enabling per-engine false-negative accounting);
- enqueues the step task (durable, write-through to `queued_tasks`);
- emits `run.step` `start` frames so **timeline nodes light at step-start**
  (not launch), and re-stamps `Result.executed_at` at real step start so MTTD is
  **detector latency**, not launch-to-click.

The Go beacon polls `/api/agents/{id}/tasks`, runs each step through the
**identity harness** (service accounts → authentic process lineage the
behavioral detections need), and POSTs per-step `/output` → `/complete`. You can
**abort** live (`POST /api/runs/{id}/abort`).

## Step 3 — Open the Detection Storyline presenter

Open **Presenter Mode** (`PresenterMode.jsx`) from the In-flight view. It:

- collapses console chrome to a full-screen stage;
- pins a live KPI header — **Coverage %**, **median MTTD**, **pending count**;
- renders the storyline via `useDetectionStoryline` — an **SSE-driven** store
  subscribed to `/api/runs/:id/events`, folding `run.step` + enriched
  `result.observed` + `run.coverage` + `run.reconcile` frames in real time.
  (It replaces the old 5-second poll; polling `GET /runs/{id}/storyline` is only
  a degraded backstop.)

A mid-demo `EventSource` reconnect never shows a blank stage: the stream replays
a `?since` / `Last-Event-ID` backfill snapshot (`sse_backfill.build_backfill`)
before draining live events.

## Step 4 — Arm the catch, then watch detections light up

Cortex ingest + analytics take ~30–120s. Rather than a dead stage, you get the
designed suspense beat:

1. **Arm reconcile.** Click **Demo Mode** (`POST /api/runs/{id}/demo-reconcile
   {on:true}` → `proof.js.armDemoReconcile`). This enables credential-backed
   auto-reconcile **for the length of this one run** — no need to flip the
   global `CORTEXSIM_AUTO_RECONCILE` env var. (Offline? Use **Pull from tenant**
   for a manual one-shot `POST /api/runs/{id}/reconcile?connector=xsiam`.)
2. **`HuntingState`** shows a scanning pulse + a ticking MTTD counter +
   *"Cortex analyzing — K of M pending,"* fed by `run.reconcile` heartbeat
   frames.
3. **`DetectionCatch` — the wow beat.** The moment a real alert reconciles, the
   *specific* chip flips `pending → detected` with a pulse, stamps the **true
   MTTD**, and renders the **Cortex-engine badge** (BIOC / XQL / Analytics /
   Correlation / IOC / ABIOC). This is "the ABIOC named X fired CRITICAL on
   `web-01`," not "an alert fired."

Under the hood: `matcher.reconcile` correlates each seeded Result on **technique
id OR firing detection id OR strong name overlap** within the time window and
takes the *earliest* qualifying alert. A bare time-only match is never accepted
(it would over-credit coverage).

## Step 5 — Capture evidence (chain of custody)

Click any detected chip to open the **`EvidenceDrawer`**. It renders the
persisted `ResultEvidence` for that detection:

- **severity / host / timestamp** of the real alert;
- **`matched_on` rationale** — *why* it was credited (e.g. "technique **and**
  detection_id matched"), with a confidence flag for weak name-only matches;
- **the actual firing detector id** (`observed_detection_id`) — the real BIOC /
  XQL / correlation rule that fired, distinct from the seeded *expected* logic;
- **"View in tenant"** — the `deep_link` opens the alert in the customer's own
  Cortex console;
- a **raw-JSON expander** — the untouched source alert record (`ObservedAlert.raw`).

Provenance is explicit per row: `source` is either the connector kind + integration
(machine-**verified**) or `manual` (**attested**).

### 6b — Offline / no-credential path

No tenant credential (or an air-gapped POV)? Two options feed the **same**
evidence surface:

- **Manual validate:** in the results grid, mark a detection observed
  (`PUT /api/results/{id}/validate`) — the layer stamps `observed_method='human'`
  and, if you paste an alert id / JSON / deep-link, lands a
  `ResultEvidence(source='manual')` row.
- **Batch observations:** `POST /api/runs/{id}/observations` with an array of
  alert dicts (offline export from the tenant) → the matcher correlates them
  exactly as the live pull does.

Everything reconciles through the same `matcher` + `evidence_capture` path, so
the scorecard math is identical — only the provenance badge differs.

## Step 6 — Export the efficacy scorecard for the CISO

Open the **`ScorecardView`** tab (`GET /api/runs/{id}/scorecard`, built by
`scorecard.build_scorecard` off the same storyline shape). It renders:

- **Per-Cortex-product coverage cards** — coverage rolled up to the SKU under
  evaluation via `PLANE_TO_PRODUCT` (EDR→XDR, CDR→Cortex Cloud, ITDR→Cortex
  ITDR, …);
- **MTTD histogram** — p50 / p90 across the run (not just avg/min/max);
- **Detection-type donut** — the mix across the six-value vocabulary;
- **Ranked gap list** — expected-but-missed, keyed by technique + engine (your
  false-negative story, defensible because the denominator is known);
- **Machine-verified-vs-attested badges** — the provenance rollup.

Then export the Cortex-branded POV report:
`GET /api/runs/{id}/report?format=markdown`. Every green box now cites its
evidence — `expected → observed alert <id> (<severity>, <host>, <ts>) →
[view in tenant]` — instead of a bare seeded description. This is the
board-ready artifact (Henry's two-section format: exec-summary paragraph +
technical appendix with evidence and deployable detections).

---

## End-to-end verification checklist

Use this to smoke-test the whole path before a customer session:

1. Launch a scenario → confirm timeline nodes light at **step-start**.
2. Arm **Demo Mode** → observe **`HuntingState`** ("K of M pending").
3. A reconciled alert **flashes a specific chip** with real MTTD + engine badge.
4. Open **`EvidenceDrawer`** → confirm deep-link opens the tenant + raw JSON expands.
5. Open **Scorecard** → confirm per-product coverage, MTTD p50/p90, detection-type mix.
6. Backend: `pytest` inside the prod image; UI: `vitest`.

## Endpoint quick reference

| Action | Endpoint |
|---|---|
| Mint enrollment token | `POST /api/agents/enroll/tokens` |
| Install / enroll agent | `GET /api/agents/install?token=…` → `POST /api/agents/enroll` |
| Launch run | `POST /api/runs` (plane/scenario) |
| Live event stream (scoped) | `GET /api/runs/{id}/events` (+ `?since` / `Last-Event-ID` backfill) |
| Storyline (poll backstop) | `GET /api/runs/{id}/storyline` |
| Scorecard | `GET /api/runs/{id}/scorecard` |
| Run-scoped evidence | `GET /api/runs/{id}/evidence` |
| Arm demo reconcile (one run) | `POST /api/runs/{id}/demo-reconcile {on}` |
| Manual one-shot reconcile | `POST /api/runs/{id}/reconcile?connector=xsiam` |
| Batch observations (offline) | `POST /api/runs/{id}/observations` |
| Manual validate (+ evidence) | `PUT /api/results/{id}/validate` |
| Branded POV report | `GET /api/runs/{id}/report?format=markdown` |

## Why this wins (elevator version)

Competitors say "an alert fired." CortexSim says "**the ABIOC named X fired
CRITICAL on `web-01` at 14:03:22, mapped to T1059.001, credited because both the
technique and the BIOC id matched — here's the raw record and a one-click link
into your tenant.**" Full positioning:
[`why-better-than-caldera.md`](why-better-than-caldera.md).
