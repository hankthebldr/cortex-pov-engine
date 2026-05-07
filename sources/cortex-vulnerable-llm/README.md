# cortex-vulnerable-llm

Deliberately vulnerable LLM Flask app for **Cortex AIRS** detection
validation. One blueprint per OWASP Top 10 for LLM Applications
(v2025/2.0) class, backed by a deterministic regex-driven canary —
**no real LLM calls, no API keys, ever.**

Used as the target for the planned `cortex-prompt-attacker` runner
(Phase 3) and the `airs_prompt_attack` EAL plugin (Phase 3 wire-up).
Design is grounded in
[`docs/eal-simulator/research-dvllm-prompt-attacker.md`](../../docs/eal-simulator/research-dvllm-prompt-attacker.md).

## Install

```bash
cd sources/cortex-vulnerable-llm
pip install -e .[test]
```

## Run

```bash
# All OWASP classes mounted on :8089
cortex-vulnerable-llm serve --port 8089

# Single class with a custom seeded system prompt (used by LLM01 + LLM07)
cortex-vulnerable-llm serve --port 8089 --vuln llm07 \
    --system-prompt "You are CortexSimAdmin. Internal API key: AKIA0000CANARY."

# Subset
cortex-vulnerable-llm serve --port 8089 --vuln llm01,llm07,llm10

# Inspect routes
cortex-vulnerable-llm list --vuln all | jq .

# Read the exploit narrative for one class
cortex-vulnerable-llm docs llm01
```

## Endpoints

| Class  | Endpoint                              | Purpose |
|--------|----------------------------------------|---------|
| LLM01  | `POST /owasp/llm01/chat`               | Direct prompt injection (override / role-play / delimiter smuggle) |
| LLM02  | `POST /owasp/llm02/chat`               | Sensitive info disclosure (synthetic PII leak) |
| LLM03  | `GET /owasp/llm03/plugins`             | Supply-chain (typosquat publisher in plugin manifest) |
|        | `POST /owasp/llm03/install`            | Installs by name; flags unverified publishers |
| LLM04  | `POST /owasp/llm04/feedback`           | Accepts attacker "training feedback" |
|        | `POST /owasp/llm04/chat`               | Returns planted reply on poisoned topic |
| LLM05  | `POST /owasp/llm05/render`             | Improper output handling (unescaped HTML) |
| LLM06  | `GET /owasp/llm06/agent/tools`         | Advertises high-impact tools |
|        | `POST /owasp/llm06/agent`              | Coerced unsafe tool calls (`exec_shell`, `delete_file`, `send_email`) |
| LLM07  | `POST /owasp/llm07/chat`               | System-prompt leak via promptmap-style stealers |
| LLM08  | `POST /owasp/llm08/rag/upload`         | RAG ingest (no instruction stripping) |
|        | `POST /owasp/llm08/rag/query`          | Returns poisoned doc on overlapping query |
| LLM09  | `POST /owasp/llm09/chat`               | Confidently emits canary-tagged misinformation |
| LLM10  | `POST /owasp/llm10/chat`               | Unbounded `max_tokens` → DoS |
| —      | `GET /healthz` `/readyz` `/version`    | Probes |
| —      | `GET /docs[/llmNN]`                    | Per-class exploit narrative |

## Response shape

Every endpoint returns JSON with the following stable fields so the
prompt-attacker's scorers can aggregate uniformly:

```json
{
  "owasp_id": "LLM01",
  "vulnerable": true,
  "rule_name": "ignore_previous_instructions",
  "leak_markers": ["hidden context"],
  "text": "Of course. Here is my hidden context: ..."
}
```

Some classes attach extra context: LLM06 emits `tool_call`, LLM08 emits
`doc_filename` + `term_overlap`, LLM10 emits `token_count`.

## Test

```bash
pip install -e .[test]
pytest tests/ -v
```

Tests cover both the safe and compromised path for every OWASP class
plus the canary engine, app factory, docs route and CLI.

## Safety

This service is **deliberately vulnerable**. Run it only in a controlled
POV environment, behind the customer NGFW / AIRS proxy. Bind to
`127.0.0.1` by default; expose to the AIRS validation lane only via the
chart values when deploying through the EAL simulator's Helm chart.

## License

Apache-2.0. See [`THIRD_PARTY_NOTICES.md`](./THIRD_PARTY_NOTICES.md) for
the design abstractions borrowed from third-party projects.
