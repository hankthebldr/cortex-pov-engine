# Roadmap

> Status is measured, not asserted. "Shipped" means it loads, has a test, and
> a gate in CI protects it. It does **not** mean it has run against a customer
> tenant — see the tenant-verified caveat on [[Home]].

## Shipped

| Area | State |
|---|---|
| Scenario corpus | **193 scenarios · 16 planes**, 0 rejected, 0 dangling refs (counts copied 2026-11-10 from `docs/reference/ground-truth.md`, which wins) |
| TTP detection cards | **191** cards, all `detection_id` slugs resolve |
| Assertions (POS/PLT/AUT) | **28** artifacts; load-time guard rejects a check that cannot fail |
| EAL simulator | **27** plugins; delivery accounted (2xx only) with a 12-code taxonomy |
| Tool adapters | **91** packs across 5 tiers; every tier-4 pack declares artifact or exemption |
| Payload shelf | 8 digest-pinned artifacts; compose-time refusal on mismatch; proven live end-to-end |
| UC/TC index | FY27 v2.2 loaded at boot; S-10..S-16 enforced, strict by default |
| Execution — pull | Beacon end-to-end incl. abort, SSE, durable queue, enrollment tokens |
| Execution — push | bash · PowerShell · K8s; platform-aware target resolution |
| Windows | **Authored, not proven.** The beacon is served for `windows/amd64` with a new SCM service handler, OS-keyed pull dispatch, Job Object tree-kill and an ASCII installer and uninstaller; `.ps1` push bundles now really run their steps and report an exit code. None of it has executed on a Windows host. |
| Measurement loop | Opt-in read-only alert read-back → evidence-backed MTTD |
| Scoring | Tier 1 offline (ungated) + Tier 2 tenant XQL (opt-in, quota-disciplined) |
| Preflight | Staged connector preflight with `queries_issued` on every response |
| IaC generator | AWS feature-complete — **11 modules** |
| DLP plane | Shipped 2026-08-30 — 5 scenarios, 5 cards, 2 POS assertions, plane descriptor, loader enum |
| Console | React SPA incl. Readiness, UC/TC Index, causality graph, live SSE run view |
| CI | 9-job matrix (8 ubuntu + `windows-2022`) + a second test workflow; PowerShell 5.1 floor gate (`make check-powershell`). The `windows` job is authored and has not yet run on GitHub. |

## Open — known and quantified

These are tracked because quantifying a gap is more useful than hiding it.

| Gap | Detail |
|---|---|
| **tenant-verified = 0** | No run and no assertion has executed against a live Cortex tenant. |
| KPI coverage | **134 of 177** scenarios (counted 2026-10-19, before the last 16 were added) declare a primary KPI the engine can measure (66 MTTD, 47 Detection Accuracy, 21 correlation rate). The other **43** score `pending` permanently. Measurable is not measured — no tenant has answered yet. |
| Index coverage | 106 of 266 index test cases authored (90 by scenario, 26 by assertion); 160 open. Only 91 carry a measurable threshold at all. Authored is not proven. |
| Windows execution | A correct PE is served, but **no Windows host has executed** the beacon, the installer, the uninstaller or a `.ps1` bundle. The `windows-2022` CI job is the first place it can; it has not run yet. Beta limits: `docs/release/mvp-launch-runbook.md` §7–§8. |
| Rust tool delivery | `rust-dist/` is baked into the image but there is no `/api/tools/binary/{tool}` route — `docker cp` is the only way out. |
| Shelf exemption surfacing | The tier-4 `reason_code` does not reach the console; all 48 exempt packs render one generic sentence. |
| Readiness preflight wire defect | The console calls a route that returns 405 for `xsiam_tenant`; the working route is `POST /api/connectors/{kind}/preflight`. |
| No release cut | `release.yml` is implemented but no `v*.*.*` tag exists, so there is no published image or release artifact. |

## Pending — planned

| Phase | Item |
|---|---|
| C | GCP provider port of all IaC modules |
| D | Azure provider port of all IaC modules |
| E | `onprem` provider (Ansible + Docker Compose) — design only |
| — | EAL dispatch to an enrolled beacon (the CampaignExecutor still runs in SimCore's own process) |

Authoritative, enumerated gap backlog:
[`docs/reference/GAP-ANALYSIS.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/reference/GAP-ANALYSIS.md).
