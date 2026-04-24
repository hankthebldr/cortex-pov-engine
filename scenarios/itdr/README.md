# scenarios/itdr

ITDR scenarios — Kerberoast, Pass-the-Hash, DCSync, MFA bypass.

These scenarios exercise Cortex ITDR (Identity Threat Detection and Response) capabilities
targeting Active Directory and identity infrastructure attacks. Scenarios leverage the
identity harness to create realistic process causality chains from legitimate service accounts.

Primary TTPs: Kerberoasting (T1558.003), Pass-the-Hash (T1550.002), DCSync (T1003.006),
MFA bypass techniques, credential stuffing.

Primary source repos: Impacket (Phase 2), identity harness (runuser/sudo-u patterns)

Use case prefix: UCS-ITDR-xx
