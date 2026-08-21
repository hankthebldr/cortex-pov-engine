# Exposure + Prevention: The Dual-Control POV Narrative

> **Audience:** Domain Consultants running a Cortex POV.
> **What it arms you with:** the sales story, the demo choreography, and the exact
> evidence surface behind *"why you need **both** exposure management **and**
> runtime process blocking — and why no single point tool can give you either
> half of the platform argument."*
>
> **Backed by:** `SIM-MP-007` (Staged Exposure → Runtime Exploit → Impact) and the
> Causality Graph projection. See `docs/reference/causality-graph-methodology.md`
> for the node/edge state machine this doc's verdicts are computed from.

---

## 1. The thesis in one sentence

**A breach requires two things at once — a standing weakness *and* a live exploit
that abuses it — so a defensible security program needs one control that finds and
shrinks the weakness *before* it is exploited, and a second control that severs the
exploit *at runtime*. Point tools each own exactly one half. Cortex owns both, and —
this is the part a point-tool stack structurally cannot replicate — stitches the two
into a single provable incident.**

That last clause is the whole POV. Anyone can sell a scanner. Anyone can sell an EDR
agent. Nobody but the platform can draw the *edge* between the exposure it found at
rest and the exploit it blocked at runtime, on the same asset, under one
`incident_id`.

---

## 2. The two halves, and what each control actually catches

| | **Exposure management** (find the weakness at rest) | **Runtime prevention** (sever the exploit live) |
|---|---|---|
| **Cortex surface** | ASM / Xpanse (external exposure), CSPM / Cortex Cloud (cloud misconfig, IAM), AI-SPM (model/supply-chain posture) | Cortex XDR agent (BIOC / ABIOC process, memory, causality blocking), CDR container-runtime detection |
| **CortexSim planes** | `asm`, `cspm`, `ai_spm` | `edr`, `cdr`, `koi`, plus the network view from `ndr` |
| **What it catches** | Public host / open security group, unauth service on the internet, over-permissive IAM role, public S3, exposed model endpoint, privileged/hostPath pod | Interactive shell spawned from a web-service account, in-memory credential read, container escape, lateral movement to an internal service, C2 beacon + exfil |
| **When it fires** | *Before* code executes — at rest, continuously | *During* execution — the moment the adversary is on the box |
| **`control_layer` tag** | `exposure` | `prevention` |
| **Cortex product row in the scorecard** | `Cortex ASM`, `Cortex Cloud`, `Cortex AI-SPM` | `Cortex XDR` |

The `control_layer` attribute on each `expected_detection` (and the
`PLANE_TO_PRODUCT` map in `core/engine/efficacy_scorecard.py:55-72`) is what lets the
efficacy scorecard render this table *from a real run* rather than a slide.

---

## 3. Why each point tool misses half — say this out loud

**A vulnerability scanner / ASM / CSPM alone knows a door is unlocked but cannot stop
a burglar already inside.** It produces a backlog of staged findings that are never
all remediated inside SLA. It is blind to zero-days and — critically — to the runtime
*behavior* of a valid-but-abused credential. A stolen AWS key is not a
"vulnerability"; it is runtime activity. Once code is executing, posture tooling has
nothing to say.

**An EDR agent alone only fires *after* the adversary is already executing on the
asset.** It generates alert volume but cannot proactively shrink the attack surface,
and the endpoint agent has no visibility into external exposure or the cloud control
plane — it never sees the public S3 bucket, the open security group, or the exposed
model endpoint. It stops today's exploit, but the same unlocked door invites
tomorrow's.

Neither tool is *wrong*. Each is doing its one job. The failure is structural: **the
finding lives in one vendor's console and the runtime block lives in another's, so
there is no shared causality graph to join them.** That join is the platform.

---

## 4. The three-beat story SIM-MP-007 walks

`SIM-MP-007` reuses the already-planted IaC assets (`infra/modules/aws/asm`'s public
`dmz-web-01` running gocortexbrokenbank on `:9001` with an unauth Redis on `:6379`
behind a `0.0.0.0/0` security group) so the *same weakness* is both the posture
finding and the exploit primitive.

**BEAT 1 — STAGE (exposure plane, no runtime execution).**
Cortex ASM/Xpanse discovers the exposed unauth Redis + web app on the public host;
CSPM flags the `0.0.0.0/0` ingress on the DB port. These are `control_layer:
exposure`, `plane: ASM`/`CSPM`, `detection_type: Analytics` posture findings — the
*"you should have caught this at rest"* beat. **No attack has run yet.**

**BEAT 2 — EXPLOIT (fast runtime).**
T1190 web RCE against gocortexbrokenbank spawns an interactive `bash` as `www-data`
(`bioc-mp-007-shell-from-www-data`), then reads credentials with an anomalous,
freshly-spawned web-service child — the first **endpoint ABIOC** outside CDR
(`abioc-mp-007-cred-access-behavioral`). These are `control_layer: prevention`,
`plane: EDR`. This is the beat only behavioral process blocking stops; the scanner
already did its job at Beat 1 and is silent.

**BEAT 3 — IMPACT + STITCH.**
Lateral movement hits the *same* exposed Redis (`redis-cli` to `:6379`), materializing
the network 5-tuple and the endpoint↔network stitch edge; then C2 beacon + exfil under
`TA0040`. The terminal `Correlation` step (`CR-MP-0007`) asserts XSIAM stitches the
ASM exposure + EDR process lineage + NDR session into **one `incident_id` spanning ≥3
planes** within a 120-second `correlation_window_seconds`.

`required_planes_in_incident: [ASM, EDR, NDR]` is deliberately *one exposure plane and
two runtime planes* — so the incident **cannot "complete" unless both control layers
fired.**

---

## 5. The proof surface: the exposure_exploit edge state

This is what makes SIM-MP-007 different from a linear storyline. The Causality Graph
derives an `exposure_exploit` edge from Beat-1's exposure node to Beat-2/4's exploit
nodes on the same asset (`dmz-web-01`). Its state IS the verdict:

| Edge state | What was observed | What it proves | Dual-control verdict |
|---|---|---|---|
| **CONFIRMED** | An exposure-plane finding (ASM/CSPM) **and** the runtime EDR/NDR blocks fired **and** stitched under one `incident_id` within 120s | The platform found the weakness at rest, blocked the exploit live, and joined them | **"Dual control confirmed."** |
| **Half-edge — prevention only** | Runtime block fired; upstream posture finding **missed** | Point EDR would have blocked *this* exploit but never shrank the surface | **"You blocked it but never shrank the surface."** |
| **Half-edge — exposure only** | Posture finding raised; runtime block **missed** | Point scanner flagged the door but could not stop the burglar | **"You flagged it but could not stop it."** |
| **BROKEN** | Both ends observed but **outside** the window | A real stitching/enrichment gap (missed source, clock skew) — higher-value POV evidence than a plain missed detection | Named blind spot |

The two half-edge strings are the two failure modes that make the platform argument
concrete, and they map directly to the dual-control verdict the scorecard renders
beside the existing `_verdict` (`efficacy_scorecard.py:302`). The
`by_control_layer` breakdown — coverage % from **Exposure** vs coverage % from
**Prevention**, side by side — is the single chart that shows neither half alone
reaches 100%.

---

## 6. The A/B/C demo choreography

Run it as three passes so the customer *watches* the half-edges resolve:

- **(A) Exposure-only** — the Beat-1 finding is raised, never remediated inside the
  SLA window, and the breach proceeds through Beats 2–3 unblocked. The scorecard's
  `Prevention` column is near-zero; the verdict reads *"you flagged it but could not
  stop it."*
- **(B) Prevention-only** — Beat-2 is blocked, but the surface was never shrunk, so
  the same exposure recurs. The `Exposure` column is near-zero; the verdict reads
  *"you blocked it but never shrank the surface."*
- **(C) Dual-control** — the surface is shrunk **and** the runtime exploit is blocked
  **and** the two stitch into one provable incident. The `exposure_exploit` edge
  reaches **CONFIRMED**; the scorecard shows coverage from **both** `Cortex
  ASM`/`Cortex Cloud` (Beat 1) **and** `Cortex XDR` (Beat 2).

Because all three passes share the *same seeded denominator* (the orchestrator seeds
one `Result` per expected detection per step, and coverage is computed once in
`detection_storyline.build_summary`), the missed/detected split per product is
self-evidently half or whole. The timeline, the scorecard, and the causality graph
can never disagree about the same run — they are three projections of one number.

---

## 7. The two-paragraph pitch a DC says to a customer

> "Your current stack is two teams looking at two screens. Your posture team sees a
> list of exposed assets they'll never fully remediate inside SLA — an unlocked door
> they can *see* but can't *guard*. Your endpoint team sees a firehose of runtime
> alerts with no idea which unlocked door let the attacker in — a burglar they can
> *tackle* but only *after* he's inside. Each team is doing its job perfectly, and
> the attacker walks straight through the seam between them, because the finding
> lives in one vendor's console and the block lives in another's. There is no line
> connecting them.
>
> Cortex is the same platform on both sides of that seam. It found the exposed Redis
> on your public host at rest — the door your scanner flagged — *and* it blocked the
> web-shell and severed the lateral move at runtime — the burglar your EDR tackled —
> *and* it drew the line between them into a single incident you can open and read
> end to end. That line is the thing you cannot buy as two products. When it's
> solid, you have dual control. When it's half-drawn, we can show you exactly which
> point tool's blind spot you're paying for."

---

## 8. Where the evidence comes from (for the technical audience)

| Claim in the pitch | Backing artifact |
|---|---|
| "found the exposed Redis at rest" | Beat-1 `exposure` detections, `plane: ASM`/`CSPM`, planted by `infra/modules/aws/asm` + `.../cspm` |
| "blocked the web-shell / severed the lateral move" | Beat-2/4 `prevention` detections, `plane: EDR`/`NDR` (incl. the first endpoint ABIOC) |
| "drew the line into a single incident" | `CR-MP-0007` correlation step + the `exposure_exploit` edge reaching CONFIRMED in the Causality Graph |
| "coverage from both control layers" | `by_control_layer` + `by_product` breakdowns in `core/engine/efficacy_scorecard.py` |
| "which point tool's blind spot you're paying for" | The half-edge verdict strings (§5) |
| "the timeline and scorecard never disagree" | Both delegate coverage/MTTD to `detection_storyline.build_summary` |

---

## 9. Related reading

- `docs/reference/causality-graph-methodology.md` — the node/edge taxonomy and the
  EXPECTED → CONFIRMED → BROKEN state machine that computes the verdicts above.
- `scenarios/multi_plane/mp-007-staged-vuln-exploit-causality.yml` — the scenario.
- `scenarios/multi_plane/mp-004-apt29-cloud-cred-theft.yml` — the earlier
  proto-dual-control shape (endpoint cred-dump → cloud API abuse → S3 exfil under one
  `incident_id`).
- `scenarios/cdr/cdr-011-k8s-goat-escape-chain.yml`,
  `scenarios/cdr/cdr-012-wildfire-in-container-misconfig.yml` — cloud-native
  misconfig-plus-runtime story shapes that predate the explicit `control_layer` tag.

---

*Generated for CortexSim — Palo Alto Networks Cortex Detection Simulation Engine.*
