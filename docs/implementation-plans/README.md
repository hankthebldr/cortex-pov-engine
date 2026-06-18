# Implementation Plans — Detection-Substrate Expansion

Executable plans turning the [detection-substrate bring-in list](../brainstorm/2026-06-16-detection-substrate-expansion.md)
into buildable work. Each plan is grounded in the real codebase (exact file change
points, schema edits, validation criteria). Authored 2026-06-16.

| # | Plan | Effort | Depends on | What it delivers |
|---|------|--------|-----------|------------------|
| 01 | [ABIOC detection type](01-abioc-detection-type.md) | M | — | `ABIOC` as a 6th first-class `detection_type` (behavioral-ML, causality-anchored) across schema/loader/catalog/coverage + first ABIOC card |
| 02 | [XDM parsing/modeling rules](02-xdm-parsing-modeling-rules.md) | M | — | Model the XDM normalization substrate (`[MODEL: dataset=…]` XQL) beneath all detections + surface it for paste-into-XSIAM |
| 03 | [CDR/K8s lab port](03-cdr-k8s-lab-port.md) | L | 01 | Wrap `xsiam-prisma-cdr-lab` into SIM-CDR-009..014 + SIM-MP-006 NGFW stitch (CDR 8 → ~14) |
| 04 | [Email-security plane](04-email-security-plane.md) | M | 02 | New `EMAIL` plane (Proofpoint TAP / M365 ingestion) + phishing/BEC scenarios |
| 05 | [KOI MCP tool-response poisoning](05-koi-mcp-tool-response-poisoning.md) | M | — | Runtime tool-response injection (vs connect-time), CVE-anchored |

## Recommended build order

1. **Framework first** — 01 (ABIOC) and 02 (XDM rules) in parallel; both are additive
   enum/schema unlocks with no content dependency. Each must keep the corpus validator
   green and the prod-image loader at 0 rejected.
2. **Content on top** — 03 (depends on 01) and 04 (depends on 02). 03 is the highest-
   volume win (port ~10 ready lab scenarios). 05 can land any time (standalone).

## Provenance

- Source bring-in list + cited research: [`../brainstorm/2026-06-16-detection-substrate-expansion.md`](../brainstorm/2026-06-16-detection-substrate-expansion.md)
- Deep-research run 1 (confirmed substrates): `wf_40dee639-fb1`
- Deep-research run 2 (open questions): `wf_483218a4-9d7` — **rate-limited / inconclusive**,
  see brainstorm doc §6 for status + captured leads to re-verify.

> These are plans, not yet built. Nothing here has been merged into the engine.
