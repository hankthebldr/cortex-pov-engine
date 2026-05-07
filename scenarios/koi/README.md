# scenarios/koi

Agentic endpoint / supply-chain risk — detection of malicious or
compromised AI agent components: MCP servers, Claude/OpenAI agent
"skills", VS Code and Chrome extensions, and language-package mirrors
that ship hidden prompt injection or post-install C2.

These scenarios target two detection paths:

1. **Code scanning** — Cortex Cloud / Prisma Cloud Code scans the
   artifact and finds the issue (typo-squat package name, overbroad
   manifest, hidden injection in instructions, suspicious post-install
   script).
2. **NGFW traffic** — when an agent or developer client fetches the
   artifact from a registry / mirror, the NGFW sees the egress shape
   (PyPI / NPM / Chrome Web Store / generic HTTPS) and matches App-ID
   plus URL-filter policy.

Static artifacts ship in `sources/cortex-malicious-agentic-pack/`
(Phase 5, shipped). The thin `agentic_egress` EAL plugin emulates the
consumer fetch (Claude Desktop, Cursor, `pip`, VS Code Marketplace,
Chrome Web Store) so the NGFW path fires without standing up a real
malicious staging server. The artifact pack contains six components,
one per scenario; the plugin tarballs the requested directory at
request time and POSTs against a target host that must be in the
campaign's `target_allowlist`.

Use case prefix: `UCS-KOI-NN`

> **Status**: scenarios are `status: active` as of Phase 5. Each step
> declares an inline campaign YAML and invokes
> `python -m scripts.eal_simulator.cli run` against the
> `agentic_egress` plugin. Cortex Cloud Code findings come from
> scanning `sources/cortex-malicious-agentic-pack/` directly; NGFW
> findings come from the plugin's outbound fetch.
