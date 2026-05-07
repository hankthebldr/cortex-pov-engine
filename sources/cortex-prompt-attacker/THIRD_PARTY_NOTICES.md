# Third-Party Notices — cortex-prompt-attacker

This package is original code (Apache-2.0). It deliberately mirrors the
*design abstractions* of the projects below; **no source files are
copied or linked**. Lint check on every PR enforces no `import promptmap`,
`from promptmap import`, etc.

## NVIDIA garak
- Repo: https://github.com/NVIDIA/garak (formerly `leondz/garak`)
- License: Apache-2.0
- Borrowed: the **Attempt dataclass field naming** and the
  **probe → detector class-attribute contract**. Field names are not
  copyrightable; mirroring the JSON shape lets existing garak tooling read
  our JSONL without translation. We do not vendor garak source.

## Microsoft PyRIT
- Repo: https://github.com/microsoft/PyRIT (was `Azure/PyRIT`)
- License: MIT
- Borrowed: the **ordered `prompt_converters` chain** abstraction. Our
  `Mutator` ABC and the `Pipeline._apply_mutators` ordering are functionally
  the same shape — single `mutate(prompt) -> str`, applied left-to-right
  before sending. Multi-turn orchestrators (TAP, Crescendo, Bandit) are
  **explicitly out of scope** — they require an adversary LLM and turn this
  package into a live red-team toolkit.

## utkusen promptmap
- Repo: https://github.com/utkusen/promptmap
- License: **GPL-3.0**
- Borrowed: the **YAML rule schema only** (data, not code). We extend with
  three CortexSim fields (`schema_version`, `owasp_id`, `mutators`,
  `scorer`/`extended_scorers`), all default-empty so vanilla promptmap
  rules still validate against our `Probe` model.
- **Strictly forbidden**: importing any promptmap source. Doing so would
  contaminate the rest of CortexSim with GPL-3.0. The CI grep on every PR
  fails the build if `promptmap` appears in any import statement.

## OWASP Top 10 for Large Language Model Applications (v2025 / 2.0)
- Source: https://genai.owasp.org/llm-top-10/
- License: CC-BY-SA-4.0
- Borrowed: the stable **code → vulnerability mapping** (`LLM01..LLM10`).
  Used as the primary key on every probe (`owasp_id`) and as the namespace
  for built-in scorers. OWASP titles are quoted in this README and the
  source code's `__init__.py`; entry bodies are not bulk-copied.
