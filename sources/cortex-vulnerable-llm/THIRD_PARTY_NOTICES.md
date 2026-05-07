# Third-Party Notices — cortex-vulnerable-llm

This package is original code (Apache-2.0). It deliberately mirrors the
*design patterns* of the projects below; no source files are copied or
linked.

## WithSecure "Damn Vulnerable LLM Agent" (DVLA)
- Repo: https://github.com/WithSecureLabs/damn-vulnerable-llm-agent
- License: Apache-2.0
- Borrowed: per-vulnerability narrative-payload pattern — every blueprint
  module ships a docstring with copy-paste exploit examples, surfaced via
  `GET /docs/llmNN`. We do **not** vendor any DVLA source code; the
  Streamlit / LangChain / SQLite stack is not used.

## OWASP Top 10 for Large Language Model Applications (v2025 / 2.0)
- Source: https://genai.owasp.org/llm-top-10/
- License: CC-BY-SA-4.0
- Borrowed: stable code → vulnerability mapping (`LLM01`..`LLM10`).
  Code titles are quoted in the package's `OWASP_TITLES` dict and in the
  README; no entry bodies are bulk-copied. The OWASP project is the
  authoritative source for the taxonomy.

## NVIDIA garak (forward-looking compatibility)
- Repo: https://github.com/NVIDIA/garak
- License: Apache-2.0
- Borrowed: response-shape conventions — the `vulnerable`, `rule_name`,
  `leak_markers` field names align with garak's `Attempt.detector_results`
  so the planned `cortex-prompt-attacker` JSONL output can be ingested by
  garak-aware tooling. Field names are not copyrightable; no schema text
  is copied.

No GPL-licensed code is imported, vendored, or linked.
