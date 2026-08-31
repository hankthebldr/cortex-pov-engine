<!-- GENERATED FILE — do not hand-edit. Run `make ground-truth` to refresh (or `python3 scripts/generate_ground_truth.py`); `make check-ground-truth` fails a PR whose committed copy drifted from the corpus. Source: scripts/generate_ground_truth.py. -->
# CortexSim — Ground Truth

Every number below comes from `python3 scripts/generate_ground_truth.py`, which runs `scripts/uctc_crosswalk_v2.2.py --report` and `detection_scanner/scripts/coverage_report.py --json` plus direct filesystem/loader counts, and cross-checks every count two independent ways before writing anything. `make check-ground-truth` fails CI if this file (or `ground-truth.json`) drifts from what's on disk. Canonical machine-readable form: [`ground-truth.json`](ground-truth.json).

## Corpus

| Metric | Value |
|---|---:|
| Loadable scenarios | 170 |
| Scenarios rejected at load | 0 |
| TTP cards | 170 |
| Detection planes | 15 |
| Step-detections | 1096 |
| Catalog detection objects | 1777 |
| ABIOC+Analytics share (step-detections) | 15.7% |
| Correlation share (step-detections) | 10.7% |
| Distinct MITRE techniques (base) | 205 (117) |
| EAL plugins | 21 |
| AWS IaC modules | 11 |

### Scenarios per plane

| Plane | Scenarios |
|---|---:|
| AIRS | 5 |
| AI_ACCESS | 6 |
| AI_SPM | 7 |
| ANALYTICS | 23 |
| ASM | 6 |
| BROWSER | 6 |
| CDR | 26 |
| CLOUD_APP | 10 |
| CSPM | 5 |
| EDR | 22 |
| EMAIL | 5 |
| ITDR | 20 |
| KOI | 8 |
| NDR | 12 |
| TIM | 9 |

### Step-detections by type

| Type | Count |
|---|---:|
| ABIOC | 129 |
| Analytics | 43 |
| BIOC | 260 |
| Correlation | 117 |
| IOC | 57 |
| XQL | 490 |

## Tool adapters

| Metric | Value |
|---|---:|
| Adapter packs | 91 |
| Per tier | `{"1": 3, "2": 1, "3": 20, "4": 56, "5": 11}` |
| Tier-4 shelf-staged | 8 |
| Tier-4 exempt | 48 |
| Tier-4 undeclared (should be 0 — `TA-13` rejects it) | 0 |
| Distinct adapters wired via `adapter_ref` | 49 |
| Scenarios wiring at least one adapter | 45 |

## Assertions (POS/PLT/AUT)

| Metric | Value |
|---|---:|
| Assertion artifacts | 20 |
| By validation class | `{"AUT": 3, "PLT": 4, "POS": 13}` |
| Rejected at load (strict) | 0 |
| Boot-verified (real `AssertionCatalog`) | True |

## UC/TC index (v2.2)

| Metric | Value |
|---|---:|
| Index TCs evidenced by a scenario | 89/266 |
| DET/HNT TCs evidenced | 70/107 |
| S-13 tier disagreements | 105 |
| S-14 posture-class-primary bindings | 13 |
| PLT assertions authored | 4/43 rows (4 artifact(s), 0 tenant-proven) |
| POS assertions authored | 2/110 rows (2 artifact(s), 0 tenant-proven) |

## HTTP routes

Boot-free static count: every `@<router>.<verb>(` decorator across `core/api/*.py` + `core/main.py`. This undercounts the live OpenAPI surface by the framework's own `/api/docs`, `/api/redoc`, `/api/openapi.json` (the `backend` CI job, which boots the app, is the heavier proof for the exact served count).

| Metric | Value |
|---|---:|
| Route decorators | 127 |
| By HTTP method | `{"api_route": 1, "delete": 4, "get": 83, "post": 34, "put": 5}` |
| `APIRouter()` instances | 24 |
| Router files (`core/api/*.py`) | 22 |

## Strict UC/TC ref validation

`tests/engine/test_corpus_refs_strict.py`: **6 passed** (green).

***tenant-verified: 0.*** No run and no assertion in this repo has ever been executed against a live Cortex tenant. Authored is not proven.

