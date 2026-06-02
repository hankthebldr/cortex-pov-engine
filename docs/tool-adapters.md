# Tool Adapter Framework

> **Status (2026-06-02):** Framework + 18 reference adapters shipped and wired
> into the engine, API, and UI. Some engine consumers and adapter tiers are
> still pending — see [§7 What's shipped vs. pending](#7-whats-shipped-vs-pending).
>
> This is the canonical doc. Background/intent: the design spec at
> [`docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md`](superpowers/specs/2026-05-19-tool-adapter-framework-design.md).
> Pack authoring quick-ref: [`tools/packs/README.md`](../tools/packs/README.md).

## 1. What it is

A single declarative abstraction (`ToolAdapter`) that tells CortexSim **where a
security tool lives, how to install it, how to invoke it, what cleanup it needs,
its dual-use safety class, and which Cortex detection plane its signal lands
on**. Scenarios reference an adapter by id (`external_tools[].adapter_ref:
TOOL-NMAP`) instead of re-deriving CLI invocations per YAML; the engine resolves
the rest.

It unifies the five pre-existing, incoherent integration paths (the static
`TOOL_REGISTRY`, IaC `content-library`, EAL plugins, per-scenario `external_tools`,
and in-tree `sources/cortex-*` toolkits) behind one model. EAL traffic plugins
stay a separate peer abstraction — adapters are for **binary/script tools**.

## 2. Where the work lives (file map)

| Concern | Path |
|---|---|
| Schema (reference) | `tools/packs/_schema.yml` |
| Adapter packs (18) | `tools/packs/<tool>.yml` |
| Pydantic loader | `core/tools/adapter_loader.py` |
| In-memory catalog (singleton) | `core/tools/adapter_catalog.py` |
| Boot load | `core/main.py` (loads catalog before scenarios) |
| Scenario resolution | `core/engine/scenario_loader.py` (`external_tools[].adapter_ref`) |
| Dispatch wiring | `core/engine/orchestrator.py` (`run_template` inlining) |
| IaC auto-pull | `core/engine/infra_generator.py` (`adapter_refs[]` → `iac_module`) |
| API | `core/api/tools.py` → `GET /api/tools/adapters`, `/adapters/{id}` |
| UI — registry | `ui/src/components/console/AdapterRegistryView.jsx` |
| UI — catalog/picker | `ui/src/components/console/ToolAdapterCatalog.jsx` |
| UI — Coverage sub-tab | `ui/src/components/console/CoverageView.jsx` ("Tool Adapters") |
| Tests | `tests/tools/test_adapter_loader.py`, `tests/tools/test_adapter_packs.py`, `tests/api/test_tools_adapters_api.py` |

> **Packaging note:** `tools/` is bundled into the Docker image (Dockerfile
> `COPY tools/`). The adapter catalog reads `<base>/tools/packs`; if the dir is
> absent the catalog loads empty and scenarios with `adapter_ref` warn at boot.

## 3. The 18-adapter catalog (current)

| Adapter | Tier | Category | Safety class |
|---|---|---|---|
| TOOL-ATOMIC-RED-TEAM | 2 | adversary-simulation | dual-use-lab-only |
| TOOL-SCAPY | 2 | network-scan | dual-use-lab-only |
| TOOL-BLOODHOUND | 3 | identity-credential | dual-use-lab-only |
| TOOL-MIMIKATZ | 3 | identity-credential | dual-use-lab-only |
| TOOL-RUBEUS | 3 | identity-credential | dual-use-lab-only |
| TOOL-EVILGINX2 | 3 | social-engineering | dual-use-lab-only |
| TOOL-GOPHISH | 3 | social-engineering | dual-use-lab-only |
| TOOL-SLIVER | 3 | c2-framework | **c2-framework** |
| TOOL-NMAP | 4 | network-scan | safe |
| TOOL-MASSCAN | 4 | network-scan | dual-use-lab-only |
| TOOL-NUCLEI | 4 | web-app | safe |
| TOOL-GOBUSTER | 4 | web-app | dual-use-lab-only |
| TOOL-SQLMAP | 4 | web-app | dual-use-lab-only |
| TOOL-PYPYKATZ | 4 | identity-credential | dual-use-lab-only |
| TOOL-TRIVY | 4 | cloud-container | safe |
| TOOL-PROWLER | 4 | cloud-container | safe |
| TOOL-KUBE-BENCH | 4 | cloud-container | safe |
| TOOL-PACU | 4 | cloud-container | dual-use-lab-only |

**By tier:** tier 2 ×2 · tier 3 ×6 · tier 4 ×10 · (tier 1 in-tree & tier 5
external: none yet). **By safety:** safe ×6 · dual-use-lab-only ×11 ·
c2-framework ×1.

## 4. The 5-tier integration model

The tier is the contract between a tool and CortexSim — it dictates install path,
execution path, and consent gate:

| Tier | Meaning | Install | Invoke |
|---|---|---|---|
| 1 | in-tree (`sources/cortex-*`) | `install.sh` from source | direct subprocess |
| 2 | git submodule | `git submodule` + `build_cmd` | direct subprocess |
| 3 | IaC-provisioned (target VM) | cloud-init via content-library | pull-mode agent + identity harness |
| 4 | runtime-fetched | `install_inline` at first use | subprocess on jumpbox/agent |
| 5 | external-only (reference) | none | never (`no_invoke: true`) |

## 5. Safety classes (consent gates)

| Class | Gate |
|---|---|
| `safe` | none (scanners, reporting) |
| `dual-use-lab-only` | lab consent at scenario launch |
| `c2-framework` | requires `c2_authorized: true` at launch; never auto-staged from a push bundle |
| `destructive` | must declare a non-empty `cleanup.commands` block (engine enforces) |

## 6. Architecture / data flow

```
tools/packs/*.yml
      │  load + validate at boot (adapter_loader.py)
      ▼
adapter_catalog  (in-memory singleton)
      ├──► scenario_loader   — resolve external_tools[].adapter_ref, warn on dangling
      ├──► orchestrator      — inline run_template at dispatch + enforce safety consent
      ├──► infra_generator   — auto-include tier-3 adapter's iac_module in the bundle
      └──► api/tools/adapters — UI registry · picker · Coverage "Tool Adapters" tab
```

## 7. What's shipped vs. pending

**Shipped & verified**
- Schema + Pydantic loader + in-memory catalog (Phase A).
- 18 reference packs across tiers 2–4 (Phase B target was 12).
- Boot load with dangling-ref warnings; `GET /api/tools/adapters[/{id}]` (live: 18).
- Scenario `adapter_ref` resolution; orchestrator `run_template` inlining.
- IaC auto-pull (`adapter_refs[]` → `iac_module`).
- **Safety consent gate wired end-to-end** — orchestrator refuses gated launches
  without consent; `POST /api/run` accepts `consent: {simulation_authorized,
  c2_authorized}`; the ③ Launch UI shows the consent prompt and blocks the
  Launch button until authorized.
- **POV report "Tools Used" section** — `core/api/runs.py` `_build_tools_used_rows()`
  resolves each `adapter_ref` to name + version + tier + category + safety +
  **license + upstream attribution** (the audit/compliance trail). Renders in
  both markdown and JSON reports.
- **First adapter-wired scenario** — `SIM-MP-002` (Kerberoast→PtH→DCSync)
  references `TOOL-RUBEUS` / `TOOL-MIMIKATZ` / `TOOL-BLOODHOUND`, proving the
  catalog → scenario → consent gate → report-audit loop (spec §10).
- UI: Adapter Registry view, Tool Adapter catalog/picker, Coverage "Tool Adapters" sub-tab.
- Tests: loader, packs, API adapter, run-lifecycle consent, and catalog-integrity suites.

**Pending**
- `core/engine/push_generator.py` — emit tier-4 adapters' install scripts into
  the self-contained push bundle (so a downloaded bundle is self-installing).
- **Tier-1 (in-tree) and tier-5 (external/reference) packs** — none authored yet.
- **Wire the remaining scenarios** to their adapters — only `SIM-MP-002` is wired
  so far; most scenarios still list legacy `external_tools` without `adapter_ref`.
- **Phase C fan-out** — remaining 🟢/🟡 verdicts from the design spec's 100-tool
  inventory (~40 target), authored in waves.
- Per-adapter CLI canary in CI (version drift guard).

## 8. Adding an adapter

See [`tools/packs/README.md`](../tools/packs/README.md). In short: copy
`_schema.yml` → `tools/packs/<tool>.yml`, fill every required field, run
`pytest tests/tools/ -v` (schema validation runs there), and ensure any
`ttp_refs[]` resolve under `detection_scanner/ttps/`. Validation rules are in
the design spec §4.

## 9. Related

- **TTP detection cards** — the detection-content side (BIOC/XQL/correlation),
  see `core/engine/ttp_catalog.py` + `detection_scanner/ttps/`. Adapters
  reference TTP cards via `ttp_refs[]`; the reverse link is `referenced_by_adapters`.
- **Strategic roadmap** — `docs/strategic-roadmap.md` (#45–#46 framework, #48 IaC auto-pull).
- **Design spec** — `docs/superpowers/specs/2026-05-19-tool-adapter-framework-design.md`.
