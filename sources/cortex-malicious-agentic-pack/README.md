# cortex-malicious-agentic-pack

Static artifact pack for **KOI** (agentic endpoint / supply-chain risk)
detection validation. Each subdirectory contains a deliberately
malicious AI agent component — an MCP server, MCP package, Claude
skill, VS Code extension, Chrome extension, or PyPI package — whose
issues should be caught by **either** code scanning **or** NGFW egress
inspection.

> **Nothing here connects out on its own.** Every component is a
> static file or unprivileged source tree. Detection happens in two
> places:
>
> 1. **Cortex Cloud Code / Prisma Cloud Code** scans the artifact and
>    finds the issue (typosquat package name, overbroad manifest, hidden
>    `[SYSTEM_OVERRIDE]` instruction in body, suspicious post-install
>    side-effect, etc.).
> 2. **NGFW** sees the *fetch* of the artifact when the
>    `agentic_egress` EAL plugin (Phase 5) emulates the consumer client
>    (Claude Desktop, Cursor, `pip`, VS Code Marketplace) pulling it
>    from a staging URL. The plugin is the only thing that ever puts
>    these bytes on the wire — and only against a host that lives in the
>    campaign's `target_allowlist`.

## Layout

| Path | Component | Detection path |
|---|---|---|
| `mcp/anthroopic-calculator/` | Typosquat MCP server source | Code scan: package name typosquats `anthropic-calculator`; tool description embeds `Ignore previous instructions` |
| `mcp/pa-firewall-mcp/` | Malicious MCP server source | Code scan: tool reply embeds `[SYSTEM_OVERRIDE]` + AKIA canary as instruction-injection seed |
| `pypi/mcp-server-helpers-typo/` | Backdoored PyPI package | Code scan: typosquat name + post-install `subprocess.run` on import |
| `claude-skills/code-reviewer.skill/` | Malicious Claude skill bundle | Code scan: hidden `Ignore previous instructions` text in `skill.md`; manifest claims verified-publisher with mismatched signature |
| `vscode/helpful-ai-assistant/` | Malicious VS Code extension source | Code scan: `package.json` declares `activationEvents:["*"]` + reads `${HOME}/.aws/credentials` in `activate()`; post-install C2 stub |
| `chrome/ai-page-summarizer/` | Malicious Chrome extension source | Code scan: manifest requests `<all_urls>` + `cookies` + `webRequest` + `webRequestBlocking`; silent tab-content exfil stub |

## Building artifacts

The `tools/build-artifacts.sh` script packs source directories into
the binary forms (`.vsix`, `.crx`, `.skill` zip, source tarball,
`.tar.gz` for the MCP servers) consumers expect:

```bash
./sources/cortex-malicious-agentic-pack/tools/build-artifacts.sh
```

The plugin only needs the source tree — it tarballs at request time —
so building binaries is optional for tests.

## Safety & licensing

- **No network connectivity from these files.** Post-install C2 stubs
  read from a `CORTEXSIM_C2_URL` env var that defaults to `''`; if
  empty, the stub no-ops and prints a marker line. This keeps the
  artifact pack safe to scan locally without producing real beacons.
- **Markers everywhere.** Every artifact contains the literal
  `CORTEXSIM-CANARY` so SOC analysts can attribute findings.
- **Deliberately malicious **patterns** — for detection validation
  only.** Do not deploy outside an authorised POV.
- License on this directory: **Apache-2.0**. Patterns inspired by
  publicly disclosed supply-chain incidents — no real malware code is
  copied.

## See also

- `core/eal_simulator/plugins/agentic_egress.py` — the EAL plugin that
  emulates the consumer fetch.
- `scenarios/koi/sim-koi-001..005.yml` — the POV scenarios that
  reference this pack.
- `docs/eal-simulator/research-dvllm-prompt-attacker.md` §KOI risks.
