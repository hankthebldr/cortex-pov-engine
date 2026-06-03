# Tool Adapter Framework

> **Status (2026-06-02):** Framework + **69 adapters across all 5 tiers**
> (Phase A/B/C complete). **27 scenarios** reference adapters; push bundles
> self-install tier-4 tools; the launch consent gate is wired end-to-end. The
> remaining design-spec work is operational (per-adapter CI version canary) and
> ongoing scenario-to-adapter wiring — see
> [§7 What's shipped vs. pending](#7-whats-shipped-vs-pending).
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

## 3. The adapter catalog (69, all 5 tiers)

**By tier:** tier 1 ×3 (in-tree) · tier 2 ×8 · tier 3 ×20 · tier 4 ×27 · tier 5
×11 (external/reference, `no_invoke`). **By safety:** safe ×33 ·
dual-use-lab-only ×32 · c2-framework ×4 (Sliver, Empire, Starkiller, Havoc).

The full list is the source of truth — see `tools/packs/*.yml` or
`GET /api/tools/adapters`. Phase A/B's 22 reference packs are below; Phase C
added 47 more (the remaining 🟢/🟡 verdicts from the design spec's 100-tool
inventory, plus tier-5 analyst-workbench / RE / sandbox references). Phase-C
packs carry a `phase-c` tag.

### Phase A/B reference packs

| Adapter | Tier | Category | Safety class |
|---|---|---|---|
| TOOL-CORTEX-PROMPT-ATTACKER | 1 | adversary-simulation | safe (in-tree) |
| TOOL-CORTEX-BROWSER-ATTACKER | 1 | adversary-simulation | safe (in-tree) |
| TOOL-CORTEX-AGENTIC-PACK | 1 | adversary-simulation | safe (in-tree) |
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
| TOOL-DEEPCE | 4 | cloud-container | dual-use-lab-only |

*(The 47 Phase-C additions — caldera, empire, nuclei-adjacent scanners, AD tools,
cloud auditors, social-eng kits, and the tier-5 references — are not tabulated
here; query `GET /api/tools/adapters` or browse `tools/packs/`.)*

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
- **Tier-1 in-tree packs** — `TOOL-CORTEX-PROMPT-ATTACKER` (AIRS),
  `TOOL-CORTEX-BROWSER-ATTACKER` (BROWSER), `TOOL-CORTEX-AGENTIC-PACK` (KOI);
  `safe` (we own the safety surface — no launch consent), so the 15 AI/Browser/
  KOI scenarios stay launchable without friction.
- **Push-bundle self-install** — `push_generator` resolves each `adapter_ref`,
  emits tier-4 `runtime_install_command`s into the bundle, **refuses** to
  auto-stage c2-framework adapters, and notes tier 1/2/3 as pre-provisioned.
  Adapter-backed tools are excluded from the bundle's hard dependency check.
- **27 scenarios wired** to adapters (up from 1):
  - AIRS×5 → prompt-attacker · BROWSER×5 → browser-attacker · KOI×5 → agentic-pack
  - EDR×5 → atomic-red-team (EDR-005 +nmap) · NDR-004 → nmap+masscan · CDR-001 → deepce
  - MP-001 → sliver · MP-002 → rubeus+mimikatz+bloodhound · MP-003 → scapy ·
    MP-004 → pacu+mimikatz+bloodhound · MP-005 → atomic+nmap
  - **Deliberately left legacy** (EAL traffic plugins / IdP emulator / posture /
    custom payloads — no differentiated tool to attribute): AI_ACCESS, AI_SPM,
    CLOUD_APP, ITDR, NDR-{001,002,003,005,006,007}, CDR-{002,003,004,005}.
- **Phase C fan-out complete** — 47 additional packs covering the design spec's
  remaining 🟢/🟡 verdicts: adversary-sim (caldera, purplesharp, aptsimulator,
  chain-reactor, scythe), C2 (empire, starkiller, havoc), scanners (recon-ng,
  nikto, whatweb, cmseek, commix, feroxbuster, chiron), AD (impacket, bloodyad,
  krbrelayup, printspoofer, tokenvator), cloud (scoutsuite, gitleaks, kubescape,
  cloudsplaining, skyark, gitgot), social-eng (set, phishery, credking,
  crosslinked), data corpora (seclists, payloadsallthethings, yara), web target
  (dvwa), pivots (frp), capture (tshark), enrichment (vt-cli).
- **Tier-5 reference packs** — 11 external/reference tools (Ghidra, radare2,
  Cutter, ILSpy, jadx, DidierStevensSuite, CAPEv2, Hayabusa, PTEF, PMA-Labs,
  INetSim) with `no_invoke` (catalog/report reference only, never executed).
- UI: Adapter Registry view, Tool Adapter catalog/picker, Coverage "Tool Adapters" sub-tab.
- Tests: loader, packs (all 69 schema-validated), API adapter, run-lifecycle
  consent, and catalog-integrity suites.

**Pending**
- Per-adapter CLI canary in CI (version drift guard — author packs pin a version;
  upstream changes can break invocation).
- Ongoing scenario→adapter wiring as new scenarios land (27 wired today).
- Tier-2/3 adapters assume the tool is submoduled / IaC-provisioned; the actual
  `sources/<tool>` submodules and content-library entries are added on demand.

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
