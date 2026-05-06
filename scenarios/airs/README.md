# scenarios/airs

Cortex AI Runtime Security — detection of attacks against deployed LLM
applications: prompt injection (direct + indirect), jailbreak chains,
function-call abuse, system-prompt leak, RAG poisoning, and unbounded
consumption.

These scenarios target an in-tree vulnerable LLM app (`sources/cortex-
vulnerable-llm/`, planned for Phase 2) backed by a "canary" LLM that
pattern-matches the prompt and returns scripted responses — no real
model API calls. Attacks are issued by `sources/cortex-prompt-attacker/`
(planned for Phase 3), which exposes a YAML-driven probe → mutator →
target → scorer pipeline.

Mapping to **OWASP Top 10 for LLM Applications (v2025/2.0)**:

| Scenario   | OWASP    | Surface                                |
|------------|----------|----------------------------------------|
| SIM-AIRS-001 | LLM01  | Direct prompt injection                |
| SIM-AIRS-002 | LLM01+LLM08 | Indirect injection via RAG document |
| SIM-AIRS-003 | LLM07  | System-prompt leakage                  |
| SIM-AIRS-004 | LLM06  | Excessive agency / tool-call abuse     |
| SIM-AIRS-005 | LLM10  | Unbounded consumption (token DoS)      |

Use case prefix: `UCS-AIRS-NN`.

> **Status**: scenarios are `status: draft` until Phase 2 + 3 ship. The
> metadata, MITRE mapping, expected detections and step structure are
> production-shape; the only thing missing is the `cortex-prompt-attacker`
> CLI that drives them.
