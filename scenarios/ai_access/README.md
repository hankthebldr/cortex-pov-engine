# scenarios/ai_access

Cortex AI Access Security — detection of employee LLM usage that crosses
data-loss or policy boundaries (PII paste, secret leakage, source-code paste,
shadow-AI usage, jailbreak prompts, cross-provider rotation).

These scenarios emit outbound HTTPS to the canonical public AI provider
endpoints (`api.openai.com`, `api.anthropic.com`,
`generativelanguage.googleapis.com`) carrying planted markers so the
customer's **NGFW + AI Access** stack sees the egress and fires.

Driven by the **`llm_provider_egress`** EAL plugin (Phase 4), which
handles per-request `X-Simulation-Run-ID` injection, provider URL/body/
auth shape, payload templating (`benign | pii | secret | source |
jailbreak`), and `target_allowlist` safety enforcement. Earlier scenario
versions used ad-hoc `curl` invocations; v1.1 standardises on the plugin.

**No real provider keys are ever used.** Bearer tokens are bogus by design
— the request fails at the provider but the egress is what triggers the
detection. This keeps POVs reproducible without any customer-tenanted
secrets.

Use case prefix: `UCS-AIACC-NN`
