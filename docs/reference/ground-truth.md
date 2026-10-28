<!-- GENERATED FILE — do not hand-edit. Run `make ground-truth` to refresh (or `python3 scripts/generate_ground_truth.py`); `make check-ground-truth` fails a PR whose committed copy drifted from the corpus. Source: scripts/generate_ground_truth.py. -->
# CortexSim — Ground Truth

Every number below comes from `python3 scripts/generate_ground_truth.py`, which runs `scripts/uctc_crosswalk_v2.2.py --report` and `detection_scanner/scripts/coverage_report.py --json` plus direct filesystem/loader counts, and cross-checks every count two independent ways before writing anything. `make check-ground-truth` fails CI if this file (or `ground-truth.json`) drifts from what's on disk. Canonical machine-readable form: [`ground-truth.json`](ground-truth.json).

## Corpus

| Metric | Value |
|---|---:|
| Loadable scenarios | 192 |
| Scenarios rejected at load | 0 |
| TTP cards | 190 |
| Detection planes | 16 |
| Step-detections | 1370 |
| Catalog detection objects | 2104 |
| ABIOC+Analytics share (step-detections) | 15.6% |
| Correlation share (step-detections) | 10.1% |
| Distinct MITRE techniques (base) | 233 (128) |
| EAL plugins | 27 |
| AWS IaC modules | 11 |

### Scenarios per plane

| Plane | Scenarios |
|---|---:|
| AIRS | 5 |
| AI_ACCESS | 6 |
| AI_SPM | 7 |
| ANALYTICS | 38 |
| ASM | 6 |
| BROWSER | 6 |
| CDR | 28 |
| CLOUD_APP | 10 |
| CSPM | 5 |
| DLP | 5 |
| EDR | 22 |
| EMAIL | 5 |
| ITDR | 20 |
| KOI | 8 |
| NDR | 12 |
| TIM | 9 |

### Signal quality

| Metric | Value |
|---|---:|
| Stitched chains | 13 |
| High-quality signals (stitched, non-marker step-detections) | 236/1370 (17.2%) |
| Marker-only steps | 100/790 (12.7%) |
| Scenarios with indicators | 14 |
| Indicators (in-band) | 87 |
| Indicators (not in-band) | 5 |

### Step-detections by type

| Type | Count |
|---|---:|
| ABIOC | 158 |
| Analytics | 56 |
| BIOC | 362 |
| Correlation | 138 |
| IOC | 119 |
| Prevention | 0 |
| XQL | 537 |

## Tool adapters

| Metric | Value |
|---|---:|
| Adapter packs | 91 |
| Per tier | `{"1": 3, "2": 1, "3": 20, "4": 56, "5": 11}` |
| Tier-4 shelf-staged | 8 |
| Tier-4 exempt | 48 |
| Tier-4 undeclared (should be 0 — `TA-13` rejects it) | 0 |
| Distinct adapters wired via `adapter_ref` | 49 |
| Scenarios wiring at least one adapter | 46 |

## Assertions (POS/PLT/AUT)

| Metric | Value |
|---|---:|
| Assertion artifacts | 28 |
| By validation class | `{"AUT": 3, "PLT": 7, "POS": 18}` |
| Rejected at load (strict) | 0 |
| Boot-verified (real `AssertionCatalog`) | True |

## UC/TC index (v2.2)

| Metric | Value |
|---|---:|
| Index TCs evidenced by a scenario | 90/266 |
| DET/HNT TCs evidenced | 70/107 |
| S-13 tier disagreements | 123 |
| S-14 posture-class-primary bindings | 14 |
| PLT assertions authored | 4/43 rows (4 artifact(s), 0 tenant-proven) |
| POS assertions authored | 2/110 rows (2 artifact(s), 0 tenant-proven) |

## HTTP routes

Boot-free static count: every `@<router>.<verb>(` decorator across `core/api/*.py` + `core/main.py`. This undercounts the live OpenAPI surface by the framework's own `/api/docs`, `/api/redoc`, `/api/openapi.json` (the `backend` CI job, which boots the app, is the heavier proof for the exact served count).

| Metric | Value |
|---|---:|
| Route decorators | 137 |
| By HTTP method | `{"api_route": 1, "delete": 5, "get": 88, "post": 37, "put": 6}` |
| `APIRouter()` instances | 25 |
| Router files (`core/api/*.py`) | 23 |

## Strict UC/TC ref validation

`tests/engine/test_corpus_refs_strict.py`: **6 passed** (green).

***tenant-verified: 0.*** No run and no assertion in this repo has ever been executed against a live Cortex tenant. Authored is not proven.

