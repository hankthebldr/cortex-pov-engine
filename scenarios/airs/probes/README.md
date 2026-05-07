# scenarios/airs/probes

Probe YAMLs consumed by `cortex-prompt-attacker`. One subdirectory per
OWASP LLM Top 10 class; file names are
`<owasp_class>/<probe_name>.yml`.

Schema: `cortex-prompt-attacker validate --probes scenarios/airs/probes/`

Each probe is a single attack: a prompt + an expected outcome (via
`scorer` + optional `extended_scorers`). Mutators may be declared per
probe (`mutators: [base64, leetspeak]`) or supplied at runtime via the
CLI's `--mutators` flag.

## Layout

| Subdir   | OWASP | What                          |
|----------|-------|-------------------------------|
| `llm01/` | LLM01 | Direct prompt injection (override / DAN / delimiter smuggle) |
| `llm02/` | LLM02 | Sensitive info disclosure (PII record extraction) |
| `llm06/` | LLM06 | Excessive agency (tool-call abuse) |
| `llm07/` | LLM07 | System prompt leakage (prompt-stealer family) |
| `llm08/` | LLM08 | Vector / embedding weakness (RAG poison trigger) |
| `llm10/` | LLM10 | Unbounded consumption (DoS) |

## Adding a probe

1. Drop a YAML file in the appropriate `llmNN/` subdirectory.
2. Set `owasp_id: LLMNN` to match the directory.
3. Pick a primary `scorer` from
   `cortex-prompt-attacker list-scorers`. Optionally add
   `extended_scorers: [...]`.
4. Optionally declare a default `mutators: [...]` chain.
5. Validate: `cortex-prompt-attacker validate --probes scenarios/airs/probes/`

## Schema reference

See the cortex-prompt-attacker README and source
(`sources/cortex-prompt-attacker/src/cortex_prompt_attacker/probes.py`).
The schema is **promptmap-compatible**: vanilla promptmap rules load
unchanged. Three CortexSim extensions (`schema_version`, `owasp_id`,
`mutators`, `scorer`/`extended_scorers`) default-empty.
