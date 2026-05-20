# tools/packs

Tool Adapter packs — one YAML file per security tool the engine knows how to drive.

Each adapter declares the tool's integration tier, install method, invocation template, safety class, and Cortex signal mapping. The engine loads every `*.yml` here at startup, validates against the Pydantic schema in `core/tools/adapter_loader.py`, and exposes the result via `core/tools/adapter_catalog.py`.

## Reference

- Schema: [`_schema.yml`](./_schema.yml)
- Design doc: [`docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md`](../../docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md)
- Loader: [`core/tools/adapter_loader.py`](../../core/tools/adapter_loader.py)
- Catalog: [`core/tools/adapter_catalog.py`](../../core/tools/adapter_catalog.py)

## Adding a new adapter

1. Copy [`_schema.yml`](./_schema.yml) to `tools/packs/<tool>.yml` and fill in every required field.
2. Run `pytest tests/tools/ -v` — schema validation happens during the test suite.
3. If the tool is `safety_class: c2-framework` or `dual-use-lab-only`, double-check the consent-gate test in `tests/tools/test_adapter_loader.py` still covers your case.
4. If the adapter has `ttp_refs:`, ensure each referenced TTP exists under `detection_scanner/ttps/` — the startup loader warns on dangling refs.

## Status

This is Phase A — framework only. The reference adapter `nmap.yml` proves the loader-to-orchestrator path end to end. Phase B (12 reference adapters across all 5 tiers) and Phase C (fan out to ~40 🟢-verdict tools from the brief) follow.
