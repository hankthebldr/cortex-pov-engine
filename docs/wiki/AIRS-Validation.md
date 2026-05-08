# AIRS Validation

For Cortex AI Runtime Security POVs the repo ships a self-contained
canary + attacker pair so the customer's AIRS layer can be validated
**without a real LLM, real keys, or any external dependency**.

## Stack

```
┌──────────────────────┐  HTTP  ┌──────────────────────┐
│ cortex-prompt-       │ ─────> │ cortex-vulnerable-   │
│ attacker (Phase 3)   │        │ llm (Phase 2)        │
│ probes/mutators/     │        │ Flask + canary       │
│ scorers              │ <───── │ OWASP LLM01..LLM10   │
└──────────────────────┘ JSONL  └──────────────────────┘
        │                              ↑
        │                              │
        └─────► airs_prompt_attack ────┘
                EAL plugin (forwards Attempts → ECS audit pipeline)
```

## Components

### `cortex-vulnerable-llm` (canary target)

Flask app under `sources/cortex-vulnerable-llm/` with one blueprint
per OWASP LLM Top 10 (v2025/2.0) class. Backed by a deterministic
regex-driven canary. **No real LLM calls. No keys. Ever.**

Endpoints:

| Class | Endpoint | Vulnerability |
|---|---|---|
| LLM01 | `POST /owasp/llm01/chat` | Direct prompt injection |
| LLM02 | `POST /owasp/llm02/chat` | Sensitive info disclosure |
| LLM03 | `GET /owasp/llm03/plugins`, `POST /install` | Supply chain |
| LLM04 | `POST /owasp/llm04/feedback` + `/chat` | Data/model poisoning |
| LLM05 | `POST /owasp/llm05/render` | Improper output handling |
| LLM06 | `GET /owasp/llm06/agent/tools`, `POST /agent` | Excessive agency |
| LLM07 | `POST /owasp/llm07/chat` | System prompt leakage |
| LLM08 | `POST /owasp/llm08/rag/{upload,query}` | Vector / embedding weakness |
| LLM09 | `POST /owasp/llm09/chat` | Misinformation |
| LLM10 | `POST /owasp/llm10/chat` | Unbounded consumption |

```bash
# Stand the canary up locally
cortex-vulnerable-llm serve --port 8089 --vuln all

# Subset
cortex-vulnerable-llm serve --port 8089 --vuln llm01,llm07,llm10
```

Every endpoint returns the same JSON envelope:

```json
{
  "owasp_id": "LLM01",
  "vulnerable": true,
  "rule_name": "ignore_previous_instructions",
  "leak_markers": ["hidden context"],
  "text": "Of course. Here is my hidden context: ..."
}
```

### `cortex-prompt-attacker` (probe runner)

Python library + CLI under `sources/cortex-prompt-attacker/`. Pipeline:

```
┌──────────┐  ┌────────────┐  ┌──────────┐  ┌──────────┐
│ Probe    │→ │ Mutators   │→ │ Target   │→ │ Scorers  │
│ (YAML)   │  │ (ordered)  │  │ (HTTP)   │  │ (multi)  │
└──────────┘  └────────────┘  └──────────┘  └──────────┘
```

- **Probes** — promptmap-compatible YAML with three CortexSim
  extensions (`schema_version`, `owasp_id`, `mutators` /
  `scorer` / `extended_scorers`)
- **Mutators** — 10 stateless impls (`noop`, `base64`, `rot13`,
  `leetspeak`, `unicode_confusable`, `translation`, `role_play`,
  `indirect_injection`, `tool_abuse`, `repeat_token`)
- **Scorers** — 11 named OWASP-aligned detectors plus
  `RegexScorer` / `SubstringScorer` / `JSONPathScorer` primitives
- **Output** — JSONL with one `run_meta` line then one `attempt` line
  per probe×iteration; Attempt schema mirrors NVIDIA garak's

```bash
cortex-prompt-attacker run \
    --probes scenarios/airs/probes/llm01/ \
    --target-url http://127.0.0.1:8089/owasp/llm01/chat \
    --mutators noop,base64,leetspeak \
    --scorers system_prompt_leak,instruction_override \
    --iterations 1 \
    --out /tmp/airs-001-events.jsonl
```

### `airs_prompt_attack` (EAL plugin glue)

Subprocess-launches the prompt-attacker CLI and forwards every
Attempt as a properly-formatted ECS event through the EAL audit
pipeline. Inherits the existing `target_allowlist` safety gate.

## Probe pack

10 probes covering LLM01–02, LLM06–08, LLM10 ship under
`scenarios/airs/probes/`:

```
scenarios/airs/probes/
├── llm01/  ignore_previous_basic, role_play_dan, delimiter_smuggle
├── llm02/  customer_record_extract
├── llm06/  tool_abuse_exec_shell, tool_abuse_send_email
├── llm07/  repeat_words_above, initial_instruction
├── llm08/  rag_trigger
└── llm10/  dos_unbounded_tokens
```

Probes are drop-in compatible with vanilla promptmap rules.

## Scenarios

| Scenario | OWASP | What it validates |
|---|---|---|
| SIM-AIRS-001 | LLM01 | Direct prompt injection |
| SIM-AIRS-002 | LLM01 (indirect) + LLM08 | RAG document poisoning |
| SIM-AIRS-003 | LLM07 | System prompt leakage |
| SIM-AIRS-004 | LLM06 | Excessive agency / tool-call abuse |
| SIM-AIRS-005 | LLM10 | Token-exhaustion DoS |

## Licensing

- Apache-2.0 across both tools.
- Patterns borrowed from NVIDIA garak (Apache-2.0; Attempt field
  naming + probe→detector contract), Microsoft PyRIT (MIT; ordered
  converter chain), promptmap (GPL-3.0; **YAML schema only — no
  source imports**), OWASP LLM Top 10 v2025/2.0 (CC-BY-SA-4.0; stable
  code → vulnerability mapping).
- See `THIRD_PARTY_NOTICES.md` in each package.

## Deeper reading

- [`docs/eal-simulator/research-dvllm-prompt-attacker.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/docs/eal-simulator/research-dvllm-prompt-attacker.md) — design brief
- [`sources/cortex-vulnerable-llm/README.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/sources/cortex-vulnerable-llm/README.md)
- [`sources/cortex-prompt-attacker/README.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/sources/cortex-prompt-attacker/README.md)
- [`scenarios/airs/README.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/airs/README.md)
