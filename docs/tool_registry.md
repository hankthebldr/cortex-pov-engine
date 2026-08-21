# Tool Registry — superseded

This page held an 11-tool table mapping external tools to techniques and planes.
It has been superseded by the **Tool Adapter Framework**: one declarative YAML
per tool under `tools/packs/<tool>.yml`, 84 packs across 5 tiers, loaded and
validated at boot and referenced from scenarios by `adapter_ref`.

- **Current doc:** [`docs/tool-adapters.md`](tool-adapters.md)
- **Pack authoring:** [`tools/packs/README.md`](../tools/packs/README.md)
- **Live catalog:** `GET /api/tools/adapters`

Kept as a redirect because `CORTEXSIM_AGENT_CONTEXT.md` (the Phase 1 build spec,
a historical record) still points here.
