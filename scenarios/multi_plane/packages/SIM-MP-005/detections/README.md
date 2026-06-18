# SIM-MP-005 — detection artifacts

Deployable detection content for this scenario, extracted from the
linked TTP card(s) and the scenario F2 verification XQL.

| File | What it is |
|------|------------|
| `correlation_rules.xql` | The XSIAM stitching rule(s) + F2 stitch verifier. The primary POV artifact — proves the multi-plane signals fuse into ONE incident. |
| `ioc_list.csv` | Atomic indicators this scenario generates (for TIM watchlist seeding). |

IOCs: 0 · correlation rules: 1.

The authoritative BIOC/XQL bodies live on the linked card under
`detection_scanner/ttps/` and are surfaced as exports under
`detection_scanner/exports/`. This file is the package-local stitching view.
