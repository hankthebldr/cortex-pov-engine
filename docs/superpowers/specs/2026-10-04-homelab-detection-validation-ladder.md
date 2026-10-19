# Homelab detection-validation ladder — design

Status: **design** (awaiting review) · 2026-10-04
Author: Henry + Claude
Scope: validate CortexSim end-to-end against a **live** XSIAM tenant (`deathstar`)
using the homelab k3s cluster, **component by component, bottom-up**, before
running full simulations. This is the first work in the repo that will move
`tenant-verified` off **0** — and it must do so honestly.

---

## 0 · The one principle

Each component answers a **different, separable** question. The engine's whole
reason to exist is that collapsing them produces a manufactured false negative:
"the detector didn't fire" and "the signal never arrived" look identical in a
POV report unless you prove them apart.

```
  ATTACK (CortexSim)            SENSOR (Cortex, pre-existing)        XSIAM (deathstar)
  ─────────────────            ─────────────────────────────        ─────────────────
  beacon runs a TTP      ─>   Cortex XDR agent observes        ─>   xdr_data        ─┐
  EAL log-streamer POST  ─>   HTTP Log Collector / Broker VM   ─>   <vendor>_*_raw  ─┼─> detector fires?
  NDR / NGFW traffic     ─>   PA-440 forwards logs             ─>   panw_ngfw_*     ─┘
       (what WE inject)         (what we are validating)            (read-back proves it)
```

**Ingestion is proven before detection.** Rungs 1–3 each prove one wire is live
(signal lands in the right dataset) using the engine's **ingestion-round-trip
canary**. Only rung 4 claims a *detection*. When rung 4 is silent you then know
*which* link broke, instead of blaming Cortex.

**The CortexSim beacon is the attacker, not the sensor.** It generates activity;
it never reports to XSIAM. The sensors (XDR agent, collector, NGFW) are what
report — all three already exist in the lab (confirmed 2026-10-04): XDR agents
enrolled to `deathstar`, PA-440 forwarding, and a collector we will stand up or
reach via the HTTP Log Collector API.

---

## 1 · Topology

| Node / device | Role in this validation |
|---|---|
| **Laptop SimCore** (`:8899`, same LAN as k3s) | orchestrator + read-back. Holds the `deathstar` credential (encrypted vault). The cluster beacon polls it. |
| **k3s** (ms-01 cp · r630 · bd790i) | hosts the **dedicated attack target** (below) running the CortexSim beacon + the toolchain the corpus needs. |
| **Cortex XDR agent** (on the target) | the endpoint sensor — observes the beacon's activity → `xdr_data`. **Already enrolled to `deathstar`.** |
| **Collector** (HTTP Log Collector `/logs/v1/event`, or a Broker VM) | the log sensor for the EAL log-streamer / email / IdP emitters. Stand up, or use the tenant's HTTP collector with its own ingest key. |
| **PA-440 NGFW** | the network sensor — **already forwarding to `deathstar`**. NDR/NGFW signal path. |
| **`deathstar` XSIAM** | the system under test. Read-only from CortexSim (`CORTEXSIM_XSIAM_ALLOW_WRITE` stays off). |

**Dedicated target (decided 2026-10-04):** a purpose-built Linux endpoint/cloud
target, not the DaemonSet. Built from `deploy/tier-d/Dockerfile.target` (login
shells + home dirs for every corpus identity, plus `python3`, `jq`, `dnsutils`,
`zip`, `nc` — the toolchain whose absence left 69 Tier-D scenarios inconclusive
on 2026-10-04) **with the Cortex XDR agent layered in**, so one workload is both
"attackable by the beacon" and "observed by XDR." Deployed as a Deployment (not
DaemonSet) so its lifecycle — deploy → enroll → run → tear down — is explicit.

---

## 2 · The ladder

Every rung uses the engine's existing primitives: the **canary** (a unique
token planted in a raw field AND the source's native field — distinguishes
*never landed* / *landed-not-normalized* / *landed-and-normalized*), **collector
preflight** (2xx = accepted), **tenant preflight** (reachable/auth/entitled),
and **reconcile + `/verify`** (read the alert back). cortex-bot / the official
Cortex MCP are a **cross-check** layered on top — not the primary path, and the
official Cortex MCP is **not yet configured in this environment**.

| Rung | Component | Inject | Read back | PASS criterion | Prereqs |
|---|---|---|---|---|---|
| **0** | Foundations | — | `POST /api/connectors/xsiam_tenant/preflight` | reachable · authorized · entitled; SimCore reachable from cluster | laptop on LAN; clock (✔ Mac is NTP-accurate — see §4) |
| **1** | **Agent / endpoint** | one minimal TTP (`SIM-EDR-002` — Tier-D proved it runs clean — or the canary step) | XQL `dataset=xdr_data` for the process / canary | the beacon's process telemetry appears in `xdr_data` | target deployed + XDR agent enrolled |
| **2** | **Collector / logs** | one EAL analytics emitter with a `canary_token` | collector preflight 2xx → XQL the target `*_raw` dataset for the canary (raw + native field) | record lands **and** normalizes | collector endpoint + ingest key |
| **3** | **Network collector / NDR** | one NDR/NGFW emitter, or real beacon network behavior | XQL `dataset=panw_ngfw_*` for the 5-tuple / canary | NGFW→XSIAM path carries the signal | PA-440 forwarding (✔) |
| **4** | **Full simulation** | a multi-stage scenario (an `SIM-MP-*` stitch) | reconcile → `/verify` → cortex-bot / MCP cross-check | the **detector fires**; first evidence-backed `tenant-verified` | rungs 1–3 green |

Rungs 1–3 run independently and can be done in any order once their sensor is
ready; the user's priority is **collector (2) first, then agent (1)**.

---

## 3 · Validation tooling

1. **Primary — the engine's own read-back.** `reconcile` (pull observed alerts,
   matcher auto-validates seeded results → MTTD) and `POST /api/runs/{id}/verify`
   (Tier-2 outbound XQL). Read-only; `xsiam_tenant` credential kind; no write path.
2. **Canary read-back** for rungs 1–3 (ingestion, before any detector): query the
   dataset for the per-run token directly. This is what separates "arrived" from
   "fired."
3. **Cross-check — cortex-bot / Cortex MCP.** Independent confirmation of the same
   alert/issue via a second code path. `alexpekarovsky/cortex-bot` is **read+write**
   (106 tools; destructive ones gated) and **extends the official Cortex MCP Server,
   which is not configured here yet.** Use only its **read** tools
   (`get_issues`, `get_alert_multi_events`, `get_contributing_events`, XQL). Its
   write tools stay off — they would breach the repo's no-write-to-Cortex rule.

---

## 4 · Prerequisites & current blockers

- **Clock — RESOLVED, with a caveat.** The Mac is NTP-accurate (offset +0.0019 s
  vs `time.apple.com`). The preflight's "836 s skew" is **not** real: the
  connector clock-preflight stage measures the age of a sampled event, not true
  tenant↔SimCore skew (a hardening-sweep finding, reported not fixed). MTTD is
  sound; **the preflight stage should be fixed** so it stops reporting a false
  degradation (see §6).
- **SimCore ↔ cluster reachability — OPEN.** The beacon polls the laptop SimCore.
  Needs the laptop on the lab LAN (it was off-LAN 2026-10-04, k3s unreachable).
  Decide the path: LAN IP, or Tailscale between cluster and laptop.
- **Collector + ingest key — OPEN.** Rung 2 needs an HTTP Log Collector on
  `deathstar` (its own ingest key, distinct from the API key 60 used for
  read-back) or a Broker VM. Create it, or deploy a collector in-cluster.
- **XDR endpoint confirmation — OPEN (quick).** Confirm the dedicated target's
  XDR agent shows as a registered endpoint in `deathstar` before rung 1.

---

## 5 · Honesty guardrails (Gate A5)

- `tenant-verified` stays **0** until rung 4 produces an `AssertionRun` / `Run`
  with a `tc_verdict` in `pass`/`fail` from a real tenant. Rungs 0–3 prove
  *plumbing*, not coverage — never report an ingestion green as a detection.
- Read-only: `CORTEXSIM_XSIAM_ALLOW_WRITE` / `_ALLOW_DESTRUCTIVE` off; cortex-bot
  write tools off.
- A zero is degraded, not ok. An empty read-back is "nothing arrived / nothing
  fired," never silently "pass."
- The `deathstar` key lives in the encrypted vault + a `0600` scratchpad env
  file, never a commit or memory. Rotate after the testing window.

---

## 6 · Engine work this surfaces (track separately)

1. **Fix the connector clock-preflight stage** so it measures true tenant↔SimCore
   skew (or renames the stage to "event freshness") instead of reporting event
   age as clock skew. Currently cries wolf.
2. **Collector deploy manifest** (k3s) + ingest-key handling, if we stand one up
   rather than use the tenant HTTP collector.
3. **Dedicated-target image**: `Dockerfile.target` + XDR agent layer + the
   toolchain that fixed the 69 inconclusive Tier-D scenarios; a k3s Deployment.

---

## 7 · Non-goals

- Replacing the engine's read-back with cortex-bot/MCP. They cross-check.
- Any write to `deathstar`.
- Claiming coverage from an ingestion-only (rung 1–3) result.
