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
(planned, Phase 5). The thin `agentic_egress` plugin emulates the
consumer fetch so the NGFW path fires without standing up a real
malicious staging server.

Use case prefix: `UCS-KOI-NN`

> **Status**: scenarios are `status: draft` until Phase 5 ships the
> artifact pack and the `agentic_egress` plugin.
