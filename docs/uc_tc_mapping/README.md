# docs/uc_tc_mapping

UC/TC cross-reference YAML schemas for mapping scenarios to use case library.

This directory contains YAML files that define the master Use Case (UC) and Test Case (TC)
reference library. Scenario YAML files reference these via `uc_ref` and `tc_ref` fields,
which are validated by the scenario loader at startup.

Each YAML file in this directory covers one detection plane's UC/TC hierarchy:
- `cdr_uc_tc.yml` — CDR plane use cases and test cases
- `edr_uc_tc.yml` — EDR plane use cases and test cases
- `ndr_uc_tc.yml` — NDR plane use cases and test cases
- `itdr_uc_tc.yml` — ITDR plane use cases and test cases
- `cloud_app_uc_tc.yml` — Cloud App Security use cases and test cases
- `multi_plane_uc_tc.yml` — Multi-plane stitching use cases

The `uctc_mapper.py` engine module reads these files to populate the UC/TC chain display
in the React UI's UCTCMapper component.
