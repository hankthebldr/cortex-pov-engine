# Kali Toolkit — Chainable Offensive Adapters + Kill-Chains

> **Added 2026-07-24 (Kali-toolkit integration pass).** 15 tier-4 offensive tool
> adapters and 2 connected, causality-strong kill-chain scenarios. All targeting is
> **lab-only** (DVWA / gocortexbrokenbank / the `asm`/`itdr` IaC modules' exposed
> hosts); every gated scenario sets `consent.simulation_authorized`. No new C2
> framework is introduced — the offensive adapters are `dual-use-lab-only` or `safe`.

## Why "chainable"

These adapters exist to be wired into **connected** kill-chains, not fired in
isolation. Each kill-chain declares the causality contract
(`cgo_anchor` + per-step `causality{parent_step, pivot}`) so the endpoint sensor
traces one CGO-rooted process spine instead of a `cortexsim-agent` star — the same
contract the flagship `SIM-MP-007` and `asm-004` exemplars use. The output of one
stage (a resolved host set, a captured hash, a validated credential) is the
`shared_entity` pivot into the next.

## The 15 new adapters

| adapter_id | tool | tier | category | safety_class | planes |
|---|---|---|---|---|---|
| `TOOL-HYDRA` | THC-Hydra | 4 | identity-credential | dual-use-lab-only | ITDR, NDR |
| `TOOL-NETEXEC` | NetExec (nxc) | 4 | identity-credential | dual-use-lab-only | ITDR |
| `TOOL-ENUM4LINUX-NG` | enum4linux-ng | 4 | network-scan | safe | ITDR, NDR |
| `TOOL-WPSCAN` | WPScan | 4 | web-app | safe | NDR |
| `TOOL-FFUF` | ffuf | 4 | web-app | dual-use-lab-only | NDR |
| `TOOL-WFUZZ` | Wfuzz | 4 | web-app | dual-use-lab-only | NDR |
| `TOOL-RESPONDER` | Responder | 4 | identity-credential | dual-use-lab-only | ITDR, NDR |
| `TOOL-METASPLOIT` | Metasploit Framework | 4 | adversary-simulation | dual-use-lab-only | EDR, NDR |
| `TOOL-JOHN` | John the Ripper | 4 | identity-credential | dual-use-lab-only | ITDR |
| `TOOL-HASHCAT` | Hashcat | 4 | identity-credential | dual-use-lab-only | ITDR |
| `TOOL-THEHARVESTER` | theHarvester | 4 | network-scan | safe | NDR, ITDR |
| `TOOL-SMBMAP` | SMBMap | 4 | network-scan | safe | ITDR, NDR |
| `TOOL-EVIL-WINRM` | Evil-WinRM | 4 | identity-credential | dual-use-lab-only | ITDR, EDR |
| `TOOL-DNSRECON` | DNSRecon | 4 | network-scan | safe | NDR |
| `TOOL-AMASS` | OWASP Amass | 4 | network-scan | safe | NDR |

All are tier 4 (runtime-fetched at dispatch): `check-adapter-sources.sh` reports them
as WARN (not-yet-fetched), never FAIL. `run_template` is each tool's native CLI — no
wrapper code.

> **Plane-enum note.** The adapter loader's `cortex_signal.planes` enum currently
> admits 10 planes (no ASM/CSPM/TIM/AI_SPM/EMAIL). Exposure-management/OSINT tools
> whose signal conceptually lands on ASM/TIM (e.g. theHarvester) declare the closest
> admitted plane(s) — `[NDR, ITDR]` — matching the existing repo convention. Unifying
> the adapter and scenario plane vocabularies is a tracked backlog item
> (`docs/reference/mechanics-review-2026-07.md`, facet 3).

## The 2 kill-chains

### `SIM-MP-019` / `TTP-2026-0140` — External → Internal (plane ANALYTICS, 12 adapters)

Recon → web-enum → web-exploit → credential-access → lateral, chained across NDR +
EDR + ITDR signal. cgo_anchor `bash`/`root`; single root at step-01.

1. **RECON** (root) — nmap / masscan / dnsrecon fan-out against the exposed lab host.
2. **WEB-ENUM** (root, pivot `shared_entity`) — whatweb / gobuster / nikto / ffuf content discovery.
3. **EXPLOIT** (www-data, pivot `exposure_exploit`) — sqlmap / metasploit land an interactive shell.
4. **CREDENTIAL ACCESS** (www-data, pivot `process_lineage`) — hydra / netexec brute-force from the shell.
5. **LATERAL MOVEMENT** (www-data, pivot `shared_entity`) — impacket authenticates to the DC with harvested creds.

Adapters: `TOOL-NMAP, TOOL-MASSCAN, TOOL-DNSRECON, TOOL-WHATWEB, TOOL-GOBUSTER,
TOOL-NIKTO, TOOL-FFUF, TOOL-SQLMAP, TOOL-METASPLOIT, TOOL-HYDRA, TOOL-NETEXEC,
TOOL-IMPACKET`.

### `SIM-ITDR-015` / `TTP-2026-0141` — Internal AD Kill-Chain (plane ITDR, 7 adapters)

Enumeration → LLMNR/NBT-NS poisoning → offline crack → credential validation → WinRM
lateral. cgo_anchor `bash`/`root`; single root at step-01; consent carries a
`target_allowlist` (`${CORTEXSIM_DC_TARGET}` / `${CORTEXSIM_AD_TARGET}`).

1. **enum4linux-ng** — SAMR/LSARPC AD enumeration against the lab DC (T1087).
2. **smbmap** (pivot `process_lineage`) — SMB share + permission enumeration (T1135).
3. **Responder** (pivot `process_lineage`) — LLMNR/NBT-NS poisoning + NetNTLMv2 capture (T1557.001).
4. **john** (pivot `shared_entity`) — offline crack of the captured hash (T1110.002).
5. **hashcat** (pivot `shared_entity`) — GPU/CPU crack of the same capture (T1110.002).
6. **netexec** (pivot `shared_entity`) — validate the cracked credential across the lab.
7. **evil-winrm** (pivot `endpoint_network_stitch`) — WinRM lateral shell (T1021.006).

Adapters: `TOOL-ENUM4LINUX-NG, TOOL-SMBMAP, TOOL-RESPONDER, TOOL-JOHN, TOOL-HASHCAT,
TOOL-NETEXEC, TOOL-EVIL-WINRM`.

Both scenarios are `push_supported` (self-contained bash bundle; the push generator
still refuses to auto-stage any c2-framework adapter) and `pull_supported` (chained
beacon execution under one anchor shell).

## Verification (2026-07-24)

- Adapter gate: 84/84 packs valid, 0 duplicate ids; `check-adapter-sources.sh` PASS=6 WARN=44 FAIL=0.
- `make validate`: 276 pass / 0 warn / 0 fail; exports deterministic.
- Loader gate (Docker prod image): `pytest tests/engine tests/tools` → 1145 passed, 11 skipped.
- Connectedness: both kill-chains have exactly one causality root; every subsequent
  step carries a typed pivot to a real predecessor (no CGO star). All detection_id
  slugs and adapter_refs resolve.
