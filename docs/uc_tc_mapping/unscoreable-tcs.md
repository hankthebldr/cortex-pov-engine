# Unscoreable test cases

Detection-backable (DET/HNT) test cases in the v2.2 master index that carry
no machine-evaluable threshold — `Qualitative pass`, `TBD`, or blank.

`engine/verifier.py` resolves these to **`not_applicable`** and never to
`pass`. A silent pass on an unscoreable test case produces a green POV
readout that means nothing, which is strictly worse than reporting no score.

- **57 of 107** DET/HNT test cases are unscoreable.
- **37** of them are bound by the current scenario corpus.

Fixing one means giving it a measurable threshold upstream in the index, then
re-exporting the snapshot. Until then the engine reports them honestly.

Regenerate with `make unscoreable-report`.

| Test case | Use case | Class | Tier | Threshold | Bound by corpus |
|---|---|---|---|---|---|
| `TC-ASM-03` | UC-ASM | HNT | MOAT | Qualitative pass | yes |
| `TC-BYOML-01` | UC-BYOML | DET | MOAT | Qualitative pass | no |
| `TC-BYOML-02` | UC-BYOML | DET | MOAT | Qualitative pass | yes |
| `TC-CDR-01` | UC-CDR | DET | LEAD | Qualitative pass | yes |
| `TC-CDR-02` | UC-CDR | DET | LEAD | Qualitative pass | yes |
| `TC-CDR-03` | UC-CDR | DET | LEAD | Qualitative pass | yes |
| `TC-CIEM-03` | UC-CIEM | DET | LEAD | Qualitative pass | yes |
| `TC-CITH-02` | UC-CITH | DET | PARITY | Qualitative pass | yes |
| `TC-CITH-03` | UC-CITH | DET | PARITY | Qualitative pass | yes |
| `TC-CITH-04` | UC-CITH | DET | PARITY | Qualitative pass | yes |
| `TC-CVM-01` | UC-CVM | DET | LEAD | Qualitative pass | yes |
| `TC-DSPM-04` | UC-DSPM | DET | LEAD | Qualitative pass | yes |
| `TC-EDR-01` | UC-EDR | DET | PARITY | Qualitative pass | no |
| `TC-EDR-02` | UC-EDR | DET | PARITY | Qualitative pass | no |
| `TC-EDR-03` | UC-EDR | DET | PARITY | Qualitative pass | yes |
| `TC-EDR-04` | UC-EDR | DET | PARITY | Qualitative pass | yes |
| `TC-EDR-05` | UC-EDR | DET | PARITY | Qualitative pass | yes |
| `TC-EDR-06` | UC-EDR | DET | PARITY | Qualitative pass | no |
| `TC-IACS-01` | UC-IACS | DET | MOAT | Qualitative pass | yes |
| `TC-IR-03` | UC-IR | DET | MOAT | Qualitative pass | yes |
| `TC-IR-04` | UC-IR | DET | PARITY | Qualitative pass | yes |
| `TC-IR-05` | UC-IR | DET | MOAT | Qualitative pass | yes |
| `TC-IR-07` | UC-IR | DET | PARITY | Qualitative pass | yes |
| `TC-IR-09` | UC-IR | DET | PARITY | Qualitative pass | no |
| `TC-IR-10` | UC-IR | DET | PARITY | Qualitative pass | no |
| `TC-IR-11` | UC-IR | DET | PARITY | Qualitative pass | no |
| `TC-IR-12` | UC-IR | DET | PARITY | Qualitative pass | yes |
| `TC-ITDR-01` | UC-ITDR | DET | LEAD | Qualitative pass | yes |
| `TC-ITDR-02` | UC-ITDR | DET | LEAD | Qualitative pass | yes |
| `TC-ITDR-03` | UC-ITDR | DET | LEAD | Qualitative pass | yes |
| `TC-ITDR-05` | UC-ITDR | DET | LEAD | Qualitative pass | yes |
| `TC-ITPA-01` | UC-ITPA | DET | MOAT | Qualitative pass | no |
| `TC-MDR-02` | UC-MDR | DET | PARITY | Qualitative pass | no |
| `TC-MSIAM-01` | UC-MSIAM | DET | MOAT | Qualitative pass | no |
| `TC-MTH-01` | UC-MTH | HNT | MOAT | Qualitative pass | no |
| `TC-NDR-01` | UC-NDR | DET | MOAT | Qualitative pass | yes |
| `TC-NDR-02` | UC-NDR | DET | MOAT | Qualitative pass | yes |
| `TC-NDR-03` | UC-NDR | DET | MOAT | Qualitative pass | yes |
| `TC-NDR-04` | UC-NDR | DET | MOAT | Qualitative pass | yes |
| `TC-NDR-06` | UC-NDR | DET | MOAT | Qualitative pass | no |
| `TC-SCA-01` | UC-SCA | DET | LEAD | Qualitative pass | yes |
| `TC-SIEM-02` | UC-SIEM | DET | LEAD | Qualitative pass | no |
| `TC-SOAR-01` | UC-SOAR | DET | MOAT | Qualitative pass | no |
| `TC-SOAR-02` | UC-SOAR | DET | MOAT | Qualitative pass | yes |
| `TC-SOAR-03` | UC-SOAR | DET | MOAT | Qualitative pass | no |
| `TC-SOT-01` | UC-SOT | DET | MOAT | Qualitative pass | no |
| `TC-SOT-03` | UC-SOT | DET | MOAT | Qualitative pass | no |
| `TC-SSCAN-01` | UC-SSCAN | DET | MOAT | Qualitative pass | yes |
| `TC-TH-01` | UC-TH | HNT | MOAT | Qualitative pass | no |
| `TC-TH-02` | UC-TH | DET | PARITY | Qualitative pass | yes |
| `TC-TH-03` | UC-TH | DET | PARITY | Qualitative pass | yes |
| `TC-TH-05` | UC-TH | HNT | PARITY | Qualitative pass | no |
| `TC-TH-06` | UC-TH | DET | PARITY | Qualitative pass | yes |
| `TC-TH-07` | UC-TH | DET | PARITY | Qualitative pass | no |
| `TC-TIM-02` | UC-TIM | DET | MOAT | Qualitative pass | yes |
| `TC-WAAS-03` | UC-WAAS | DET | MOAT | Qualitative pass | yes |
| `TC-WAAS-04` | UC-WAAS | DET | MOAT | Qualitative pass | yes |
