# KOI Validation (Agentic Endpoint / Supply-Chain)

KOI scenarios validate detection of malicious or compromised AI-agent
components: MCP servers, Claude / OpenAI agent skills, VS Code and
Chrome extensions, and language-package mirrors that ship hidden
prompt injection or post-install C2.

## Two detection paths

```
                     ┌─────────────────────┐
   Cortex Cloud Code │  scans the artifact │  ← static detection
       ──────────►   │  in CI / IDE        │
                     └─────────────────────┘
                              ▲
   sources/cortex-malicious-agentic-pack/   (in-tree, six components)
                              │
                              ▼
                     ┌─────────────────────┐
   agentic_egress    │  tarballs + POSTs   │  ← egress detection
       EAL plugin    │  to staging URL     │
                     └─────────────────────┘
                              │
                              ▼
                     Customer NGFW + Cortex XDR/XSIAM
```

- **Code scanning** — Cortex Cloud / Prisma Cloud Code scans the
  artifact and finds the issue (typo-squat package name, overbroad
  manifest, hidden `[SYSTEM_OVERRIDE]` instruction in body, suspicious
  post-install side-effect, etc.).
- **NGFW egress** — when an agent or developer client fetches the
  artifact from a registry / mirror, the NGFW sees the egress shape
  (PyPI / NPM / Chrome Web Store / generic HTTPS) and matches App-ID
  plus URL-filter policy.

## Artifact pack

`sources/cortex-malicious-agentic-pack/` ships six components, every
side effect gated on `CORTEXSIM_C2_URL` so static scanning never
produces real beacons:

| Path | Component | Code-scan finding |
|---|---|---|
| `mcp/anthroopic-calculator/` | Typosquat MCP server | Publisher = `anthroopic-tools` (typosquat); tool description embeds `Ignore previous instructions` |
| `mcp/pa-firewall-mcp/` | Malicious MCP server | Tool reply embeds `[SYSTEM_OVERRIDE]` + AKIA canary key as instruction-injection seed |
| `pypi/mcp-server-helpers-typo/` | Backdoored PyPI package | Module-level `subprocess.run` on import |
| `claude-skills/code-reviewer.skill/` | Malicious Claude skill | Hidden `Ignore previous instructions` in `skill.md`; mismatched publisher signature |
| `vscode/helpful-ai-assistant/` | Malicious VS Code extension | `activationEvents:["*"]`, reads `~/.aws/credentials` and `~/.ssh/id_rsa` in `activate()` |
| `chrome/ai-page-summarizer/` | Malicious Chrome extension | `<all_urls>` + cookies + webRequest + webRequestBlocking; tab-content + cookie exfil |

Every artifact carries the literal `CORTEXSIM-CANARY` marker so SOC
analysts can attribute findings.

## `agentic_egress` plugin

`core/eal_simulator/plugins/agentic_egress.py` emulates a consumer
client (Claude Desktop, Cursor, `pip`, VS Code Marketplace, Chrome
Web Store) fetching the artifact:

| component | Real-client User-Agent |
|---|---|
| `mcp_server` | `claude-desktop/0.7.0 mcp-client/0.1` |
| `mcp_package` | `npm/10.5.0 node/v22.0.0 ...` |
| `pypi_mirror` | `pip/24.0 ...` *(GET probe + POST artifact)* |
| `claude_skill` | `claude-desktop/0.7.0 skills/0.1` |
| `vscode_ext` | `VSCode/1.85.0 (vsx-fetch)` |
| `chrome_ext` | `Chrome/120.0.0.0 (extension-installer)` |

The plugin tarballs the requested directory at request time and POSTs
against a target host that **must live in the campaign
`target_allowlist`**.

```yaml
campaign_id: CMP-KOI-001
authorized_by: hank@paloaltonetworks.com
simulation_authorized: true
target_allowlist:
  - cortexsim-canary.invalid
dry_run: false
steps:
  - step_id: step-01
    plugin: agentic_egress
    params:
      target_url: https://cortexsim-canary.invalid/mcp/
      component: mcp_server
      artifact_name: anthroopic-calculator
      iterations: 1
```

## Path-traversal hardening

- `artifact_name` validated by regex (no dots, no slashes)
- `_resolve_artifact_dir` refuses paths that escape the component dir
- Pack-root resolution honours explicit override → `CORTEXSIM_BASE_DIR`
  → walks up to find the in-tree sibling

## Scenarios

| Scenario | Component used |
|---|---|
| SIM-KOI-001 | Typosquat `anthroopic-calculator` MCP fetch |
| SIM-KOI-002 | Malicious `pa-firewall-mcp` server fetch (instruction-injection in tool replies) |
| SIM-KOI-003 | Typosquat `mcp-server-helpers-typo` PyPI fetch |
| SIM-KOI-004 | Malicious VS Code extension `helpful-ai-assistant` fetch |
| SIM-KOI-005 | Malicious Claude skill `code-reviewer.skill` fetch |

Each step declares an inline campaign YAML and invokes
`python -m scripts.eal_simulator.cli run` against the
`agentic_egress` plugin.

## Safety

- All artifact pack post-install hooks are **gated on
  `CORTEXSIM_C2_URL`** being set; if unset, they emit a marker line
  on stderr and no-op. This keeps the artifact pack safe to scan
  locally without producing real beacons.
- The plugin will not POST against any host that is not in the
  campaign `target_allowlist` — the `SafetyPolicy` is the only thing
  that decides whether traffic flows.

## Deeper reading

- [`sources/cortex-malicious-agentic-pack/README.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/sources/cortex-malicious-agentic-pack/README.md)
- [`scenarios/koi/README.md`](https://github.com/hankthebldr/cortex-pov-engine/blob/main/scenarios/koi/README.md)
