# Operation Catalog — authoritative index
> The machine-readable mirror of the declarative operation packs under
> `core/integrations/xsiam/operations/packs/`. Every row here is a callable
> operation in the API harness (`GET /api/xsiam/operations`,
> `POST /api/xsiam/tenants/{name}/operations/{op_id}`). This table is generated
> from the loaded catalog — if the packs change, regenerate it.
>
> **116 operations** across **23 categories** — 57 read · 43 write · 16 destructive.
## Access classes & gating
| Class | Badge | Executor behavior |
|---|---|---|
| `read` | ✅ | Runs live against a configured tenant. |
| `write` | ⚠ | **Dry-run by default.** Live call needs `CORTEXSIM_XSIAM_ALLOW_WRITE=1` **and** `consent.write_authorized=true`. |
| `destructive` | 🚫 | **Dry-run by default.** Live call needs `CORTEXSIM_XSIAM_ALLOW_DESTRUCTIVE=1` **and** `consent.destructive_authorized=true`. |
> Ops tagged `confirm-tail` were not in the source CSV (truncated at row 96); they come from the already-documented read surface and should be confirmed against the live tenant portal.

## API Keys
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-API-KEYS-DELETE` | POST | `/public_api/v1/api_keys/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/rn2mv5j2xofd4-delete-api-keys) |
| `OP-API-KEYS-GENERATE` | POST | `/public_api/v1/api_keys/generate` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/w9fmnq8q0rit1-generate-an-api-key) |
| `OP-API-KEYS-GET-API-KEYS` | POST | `/public_api/v1/api_keys/get_api_keys` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/jw4z2kkdty75e-get-existing-api-keys) |

## Alerts
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-ALERTS-GET-ALERTS-MULTI-EVENTS` | POST | `/public_api/v1/alerts/get_alerts_multi_events` | ✅ read | `confirm-tail` |
| `OP-ALERTS-INSERT-CEF-ALERTS` | POST | `/public_api/v1/alerts/insert_cef_alerts` | ⚠ write | `confirm-tail` |
| `OP-ALERTS-INSERT-PARSED-ALERTS` | POST | `/public_api/v1/alerts/insert_parsed_alerts` | ⚠ write | `confirm-tail` |

## Asset groups
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-ASSET-GROUPS` | POST | `/public_api/v1/asset-groups` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/f2a40e33e63d7-get-all-or-filtered-asset-groups) |
| `OP-ASSET-GROUPS-CREATE` | POST | `/public_api/v1/asset-groups/create` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/3deb239d99621-create-an-asset-group) |
| `OP-ASSET-GROUPS-DELETE-GROUP-ID` | POST | `/public_api/v1/asset-groups/delete/{group_id}` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/675f30cae4932-delete-an-asset-group) |
| `OP-ASSET-GROUPS-UPDATE-GROUP-ID` | POST | `/public_api/v1/asset-groups/update/{group_id}` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/955fa90608ea7-update-an-asset-group) |

## Asset inventory
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-ASSETS` | POST | `/public_api/v1/assets` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/fed2351a8c999-get-all-or-filtered-assets) |
| `OP-ASSETS-ENUM-FIELD-NAME` | GET | `/public_api/v1/assets/enum/{field_name}` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/f9605d8505dd4-get-enum-values-of-specified-field) |
| `OP-ASSETS-ID` | GET | `/public_api/v1/assets/{id}` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/fveqneo44ui9b-get-asset-by-id) |
| `OP-ASSETS-ID-RAW-FIELDS` | GET | `/public_api/v1/assets/{id}/raw_fields` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/558aa6d5460af-get-raw-fields-of-asset-by-id) |
| `OP-ASSETS-SCHEMA` | GET | `/public_api/v1/assets/schema` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/c3acd957a0efc-get-schema-of-asset-inventory) |

## Attack surface management
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-ASM-MANAGEMENT-REMOVE-ASM-DATA` | POST | `/public_api/v1/asm_management/remove_asm_data` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/m2mh8rdtamd7j-remove-assets) |
| `OP-ASM-MANAGEMENT-UPLOAD-ASM-DATA` | POST | `/public_api/v1/asm_management/upload_asm_data` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/y4rq33rcr39cz-upload-assets-to-the-inventory) |
| `OP-ASSETS-BULK-UPDATE-VULNERABILITY-TESTS` | POST | `/public_api/v1/assets/bulk_update_vulnerability_tests` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ex0fkjq6ddqqv-bulk-update-vulnerability-tests) |
| `OP-ASSETS-GET-ASSET-INTERNET-EXPOSURE` | POST | `/public_api/v1/assets/get_asset_internet_exposure` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/2bsjkufb13krj-get-internet-exposure) |
| `OP-ASSETS-GET-ASSETS-INTERNET-EXPOSURE` | POST | `/public_api/v1/assets/get_assets_internet_exposure` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/rs6v6v1bp5yz7-get-all-internet-exposures) |
| `OP-ASSETS-GET-EXTERNAL-IP-ADDRESS-RANGE` | POST | `/public_api/v1/assets/get_external_ip_address_range` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/0pddi7pfvmywy-get-external-ip-address-range) |
| `OP-ASSETS-GET-EXTERNAL-IP-ADDRESS-RANGES` | POST | `/public_api/v1/assets/get_external_ip_address_ranges` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/4hqamad5c9sx8-get-all-external-ip-address-ranges) |
| `OP-ASSETS-GET-EXTERNAL-SERVICE` | POST | `/public_api/v1/assets/get_external_service` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/8nuq8sfzvv5ft-get-external-service) |
| `OP-ASSETS-GET-EXTERNAL-SERVICES` | POST | `/public_api/v1/assets/get_external_services` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/yv4dl9libk83r-get-all-services) |
| `OP-ASSETS-GET-EXTERNAL-WEBSITE` | POST | `/public_api/v1/assets/get_external_website` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/d9eth6lamzn3a-get-website-details) |
| `OP-ASSETS-GET-EXTERNAL-WEBSITES` | POST | `/public_api/v1/assets/get_external_websites` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/dmibn6xk1gq57-get-all-websites) |
| `OP-ASSETS-GET-EXTERNAL-WEBSITES-LAST-EXTERNAL-ASSESSMENT` | POST | `/public_api/v1/assets/get_external_websites/last_external_assessment` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/t7mn07osbjc4v-get-websites-last-assessment) |
| `OP-ASSETS-GET-VULNERABILITY-TESTS` | POST | `/public_api/v1/assets/get_vulnerability_tests` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/80za5trcdioc7-get-vulnerability-tests) |
| `OP-GET-ATTACK-SURFACE-RULES` | POST | `/public_api/v1/get_attack_surface_rules` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/5foznb1borwdz-get-all-attack-surface-rules) |

## Audit log
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-AUDITS-AGENTS-REPORTS` | POST | `/public_api/v1/audits/agents_reports` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/bj6uc1r6f4z9a-get-audit-agent-report) |
| `OP-AUDITS-MANAGEMENT-LOGS` | POST | `/public_api/v1/audits/management_logs` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/fm9q36cnnrp1f-get-audit-management-log) |

## Authentication settings
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-AUTHENTICATION-SETTINGS-CREATE` | POST | `/public_api/v1/authentication-settings/create` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/5l3oodszqi37p-create-authentication-settings-for-id-p-sso-or-metadata-url) |
| `OP-AUTHENTICATION-SETTINGS-DELETE` | POST | `/public_api/v1/authentication-settings/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/gmpgtevk3baqr-delete-authentication-settings-by-domain) |
| `OP-AUTHENTICATION-SETTINGS-GET-METADATA` | POST | `/public_api/v1/authentication-settings/get/metadata` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ex29erdded5z7-get-id-p-metadata) |
| `OP-AUTHENTICATION-SETTINGS-GET-SETTINGS` | POST | `/public_api/v1/authentication-settings/get/settings` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/nhqz0tta8z99m-get-authentication-settings-for-all-configured-domains) |
| `OP-AUTHENTICATION-SETTINGS-UPDATE` | POST | `/public_api/v1/authentication-settings/update` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ecvad2y47sogk-update-authentication-settings) |

## BIOCs
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-BIOC-DELETE` | POST | `/public_api/v1/bioc/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ueke4xm8uoaha-delete-bio-cs) |
| `OP-BIOC-GET` | POST | `/public_api/v1/bioc/get` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/fs6edxqfk9957-get-bio-cs) |
| `OP-BIOC-INSERT` | POST | `/public_api/v1/bioc/insert` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/7jub2d42k5d6x-insert-or-update-bio-cs) |

## Correlation Rules
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-CORRELATIONS-DELETE` | POST | `/public_api/v1/correlations/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/78wvp0dby10uz-delete-correlation-rules) |
| `OP-CORRELATIONS-GET` | POST | `/public_api/v1/correlations/get` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/8zu0di9ad9nwm-get-correlation-rules) |
| `OP-CORRELATIONS-INSERT` | POST | `/public_api/v1/correlations/insert` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/dqsouie24u615-insert-or-update-correlation-rules) |

## Cortex CLI
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-CLI-RELEASES-VERSION` | GET | `/public_api/v1/cli/releases/version` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/s2blvuymsomtz-get-the-latest-version-of-the-cortex-cli) |

## Dashboards
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-DASHBOARDS-DELETE` | POST | `/public_api/v1/dashboards/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/sw5z379fbcbix-delete-dashboards) |
| `OP-DASHBOARDS-GET` | POST | `/public_api/v1/dashboards/get` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/k58hffooxe978-get-dashboards) |
| `OP-DASHBOARDS-INSERT` | POST | `/public_api/v1/dashboards/insert` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/4l206rch3z7fw-insert-or-update-dashboards) |

## Dataset Management
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-XQL-ADD-DATASET` | POST | `/public_api/v1/xql/add_dataset` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/iuos0v5ns8b5k-add-dataset) |
| `OP-XQL-DELETE-DATASET` | POST | `/public_api/v2/xql/delete_dataset` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/psfk3y0hseojo-delete-a-dataset) |
| `OP-XQL-GET-DATASETS` | POST | `/public_api/v1/xql/get_datasets` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/75b9xbso53qb2-get-all-datasets) |

## Endpoint Management
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-DEVICE-CONTROL-GET-VIOLATIONS` | POST | `/public_api/v1/device_control/get_violations` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/9vks6oalpiebt-get-violations) |
| `OP-DISTRIBUTIONS-CREATE` | POST | `/public_api/v1/distributions/create` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/q3wribur9dylf-create-distributions) |
| `OP-DISTRIBUTIONS-DELETE` | POST | `/public_api/v1/distributions/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/2idioh1xzgpn4-delete-agent-installation-packages) |
| `OP-DISTRIBUTIONS-GET-DIST-URL` | POST | `/public_api/v1/distributions/get_dist_url` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/sjkg3r5qdfqvu-get-distribution-url) |
| `OP-DISTRIBUTIONS-GET-DISTRIBUTIONS` | POST | `/public_api/v1/distributions/get_distributions` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/3fabtmrc8nmjv-get-distributions) |
| `OP-DISTRIBUTIONS-GET-STATUS` | POST | `/public_api/v1/distributions/get_status` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/og7h4cvkon4pa-get-distribution-status) |
| `OP-DISTRIBUTIONS-GET-VERSIONS` | POST | `/public_api/v1/distributions/get_versions` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/riio6dzgcx9jq-get-distribution-version) |
| `OP-ENDPOINTS-DELETE` | POST | `/public_api/v1/endpoints/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/r0anrn95u1yyt-delete-endpoints) |
| `OP-ENDPOINTS-GET-ENDPOINT` | POST | `/public_api/v1/endpoints/get_endpoint` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/nxq2mf3ia6wrd-get-endpoint) |
| `OP-ENDPOINTS-GET-ENDPOINTS` | POST | `/public_api/v1/endpoints/get_endpoints` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/josa75msg4xym-get-all-endpoints) |
| `OP-ENDPOINTS-GET-POLICY` | POST | `/public_api/v1/endpoints/get_policy` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/04crccks4jk2o-get-policy) |
| `OP-ENDPOINTS-GET-PROFILES` | POST | `/public_api/v1/endpoints/get_profiles` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/8e9bnkte8t8v8-get-endpoint-security-profiles) |
| `OP-ENDPOINTS-UPDATE-AGENT-NAME` | POST | `/public_api/v1/endpoints/update_agent_name` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/qmhtgk4ura8kw-set-an-endpoint-alias) |
| `OP-ENDPOINTS-UPGRADE` | POST | `/public_api/v1/endpoints/upgrade` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/85a3df45b1af1-upgrade-agents) |
| `OP-LEGACY-EXCEPTIONS-ADD` | POST | `/public_api/v1/legacy_exceptions/add` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/def84d8bd0b52-add-legacy-exception-rule) |
| `OP-LEGACY-EXCEPTIONS-DELETE` | POST | `/public_api/v1/legacy_exceptions/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/b401aa313475a-delete-legacy-exception-rules) |
| `OP-LEGACY-EXCEPTIONS-EDIT` | POST | `/public_api/v1/legacy_exceptions/edit` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/83edf03720f7c-edit-legacy-exception-rule) |
| `OP-LEGACY-EXCEPTIONS-FETCH` | POST | `/public_api/v1/legacy_exceptions/fetch` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/e2b51ac322de9-fetch-legacy-exception-rules) |
| `OP-LEGACY-EXCEPTIONS-GET-MODULES` | POST | `/public_api/v1/legacy_exceptions/get_modules` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/088215b260fc2-get-legacy-exceptions-modules) |
| `OP-POLICIES-PREVENTION-EDIT` | POST | `/public_api/v1/policies/prevention/edit` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/e5ee06a5f1532-edit-prevention-policy-rules) |
| `OP-PROFILES-ADD-SIGNER-CN-TO-ALLOWLIST` | POST | `/public_api/v1/profiles/add_signer_cn_to_allowlist` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/d69c78ed7835c-add-signer-cn-to-allowlist) |
| `OP-PROFILES-PREVENTION-ADD` | POST | `/public_api/v1/profiles/prevention/add` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/5bb092543923b-add-prevention-profile) |
| `OP-PROFILES-PREVENTION-EDIT` | POST | `/public_api/v1/profiles/prevention/edit` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/f0566ac4764eb-edit-prevention-profile) |
| `OP-PROFILES-PREVENTION-GET-MODULES` | POST | `/public_api/v1/profiles/prevention/get_modules` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/3cc60f5ee327f-get-prevention-profile-modules) |
| `OP-TAGS-AGENTS-ASSIGN` | POST | `/public_api/v1/tags/agents/assign` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/1h0jz8q3by0gs-assign-tags) |
| `OP-TAGS-AGENTS-DELETE-PERMANENTLY` | POST | `/public_api/v1/tags/agents/delete_permanently` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/7e1f05dae37d9-delete-tags-permanently) |
| `OP-TAGS-AGENTS-REMOVE` | POST | `/public_api/v1/tags/agents/remove` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/o7p7d05xyoh18-remove-tags) |
| `OP-TRIAGE-ENDPOINT` | POST | `/public_api/v1/triage_endpoint` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/7fcczcxp7x3h6-initiate-forensics-triage) |

## Health
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-HEALTHCHECK` | GET | `/public_api/v1/healthcheck` | ✅ read | `confirm-tail` |

## IOCs
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-INDICATORS-DELETE` | POST | `/public_api/v1/indicators/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/8k2z33znsby6w-delete-indicators-io-cs) |
| `OP-INDICATORS-GET` | POST | `/public_api/v1/indicators/get` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/3n8jxnuxhy57s-get-indicators-io-cs) |
| `OP-INDICATORS-INSERT` | POST | `/public_api/v1/indicators/insert` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/vmlrb0v0w8ext-insert-or-update-io-cs) |

## Incidents
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-INCIDENTS-GET-INCIDENT-EXTRA-DATA` | POST | `/public_api/v1/incidents/get_incident_extra_data` | ✅ read | `confirm-tail` |
| `OP-INCIDENTS-GET-INCIDENTS` | POST | `/public_api/v1/incidents/get_incidents` | ✅ read | `confirm-tail` |
| `OP-INCIDENTS-UPDATE-INCIDENT` | POST | `/public_api/v1/incidents/update_incident` | ⚠ write | `confirm-tail` |

## Indicator rules
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-INDICATORS-INSERT-CSV` | POST | `/public_api/v1/indicators/insert_csv` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/2vloefum4wu9r-insert-simple-indicators-csv) |
| `OP-INDICATORS-INSERT-JSONS` | POST | `/public_api/v1/indicators/insert_jsons` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ndxpbus7azvf0-insert-simple-indicators-json) |

## Lookup Datasets
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-XQL-LOOKUPS-ADD-DATA` | POST | `/public_api/v1/xql/lookups/add_data` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/rti2bc7scp749-add-or-update-data-in-a-lookup-dataset) |
| `OP-XQL-LOOKUPS-GET-DATA` | POST | `/public_api/v1/xql/lookups/get_data` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/d0c74jng28f5s-get-data-from-a-lookup-dataset) |
| `OP-XQL-LOOKUPS-REMOVE-DATA` | POST | `/public_api/v1/xql/lookups/remove_data` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/9py3apgtkh8o4-remove-data-from-a-lookup-dataset) |

## Playbooks
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-PLAYBOOKS-DELETE` | POST | `/public_api/v1/playbooks/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ee099fa49ba0d-delete-a-playbook) |
| `OP-PLAYBOOKS-GET` | POST | `/public_api/v1/playbooks/get` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/9bxk3d1x20w62-get-a-playbook) |
| `OP-PLAYBOOKS-INSERT` | POST | `/public_api/v1/playbooks/insert` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/nyd2ep538b093-insert-or-update-playbooks) |

## Query Library
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-XQL-LIBRARY-DELETE` | POST | `/public_api/xql_library/delete` | 🚫 destructive | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/drsuk452hzlex-delete-xql-queries) |
| `OP-XQL-LIBRARY-GET` | POST | `/public_api/xql_library/get` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/4bl2e1wi5yr3n-get-xql-queries) |
| `OP-XQL-LIBRARY-INSERT` | POST | `/public_api/xql_library/insert` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/y8yaywvxhsg76-insert-or-update-xql-queries) |

## Response Action
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-ACTIONS-FILE-RETRIEVAL-DETAILS` | POST | `/public_api/v1/actions/file_retrieval_details` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ifeyx54qbusiq-file-retrieval-details) |
| `OP-ENDPOINTS-ABORT-SCAN` | POST | `/public_api/v1/endpoints/abort_scan` | ⚠ write | `confirm-tail` |
| `OP-ENDPOINTS-FILE-RETRIEVAL` | POST | `/public_api/v1/endpoints/file_retrieval` | ⚠ write | `confirm-tail` |
| `OP-ENDPOINTS-ISOLATE` | POST | `/public_api/v1/endpoints/isolate` | ⚠ write | `confirm-tail` |
| `OP-ENDPOINTS-QUARANTINE` | POST | `/public_api/v1/endpoints/quarantine` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/rd1ptd5jtusi2-quarantine-files) |
| `OP-ENDPOINTS-RESTORE` | POST | `/public_api/v1/endpoints/restore` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/lslt3zwmpw7p9-restore-file) |
| `OP-ENDPOINTS-SCAN` | POST | `/public_api/v1/endpoints/scan` | ⚠ write | `confirm-tail` |
| `OP-ENDPOINTS-UNISOLATE` | POST | `/public_api/v1/endpoints/unisolate` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/lfv45czgt4kd6-unisolate-endpoints) |
| `OP-HASH-EXCEPTIONS-ALLOWLIST` | POST | `/public_api/v1/hash_exceptions/allowlist` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/md2df8eqgx7fr-allow-list-files) |
| `OP-HASH-EXCEPTIONS-BLOCKLIST` | POST | `/public_api/v1/hash_exceptions/blocklist` | ⚠ write | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/bugt5tvao9rls-block-list-files) |
| `OP-QUARANTINE-STATUS` | POST | `/public_api/v1/quarantine/status` | ✅ read | [↗](https://cortex-panw.stoplight.io/docs/cortex-xsiam-3-x/ay663dcgsvgv6-get-quarantine-status) |

## Scripts
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-SCRIPTS-GET-SCRIPT-EXECUTION-RESULTS` | POST | `/public_api/v1/scripts/get_script_execution_results` | ✅ read | `confirm-tail` |
| `OP-SCRIPTS-GET-SCRIPT-EXECUTION-STATUS` | POST | `/public_api/v1/scripts/get_script_execution_status` | ✅ read | `confirm-tail` |
| `OP-SCRIPTS-GET-SCRIPT-METADATA` | POST | `/public_api/v1/scripts/get_script_metadata` | ✅ read | `confirm-tail` |
| `OP-SCRIPTS-GET-SCRIPTS` | POST | `/public_api/v1/scripts/get_scripts` | ✅ read | `confirm-tail` |
| `OP-SCRIPTS-RUN-SCRIPT` | POST | `/public_api/v1/scripts/run_script` | ⚠ write | `confirm-tail` |
| `OP-SCRIPTS-RUN-SNIPPET-CODE-SCRIPT` | POST | `/public_api/v1/scripts/run_snippet_code_script` | ⚠ write | `confirm-tail` |

## XQL
| Op ID | Method | Path | Access | Doc |
|---|---|---|---|---|
| `OP-XQL-GET-QUERY-RESULTS` | POST | `/public_api/v1/xql/get_query_results` | ✅ read | `confirm-tail` |
| `OP-XQL-GET-QUERY-RESULTS-STREAM` | POST | `/public_api/v1/xql/get_query_results_stream` | ✅ read | `confirm-tail` |
| `OP-XQL-GET-QUOTA` | POST | `/public_api/v1/xql/get_quota` | ✅ read | `confirm-tail` |
| `OP-XQL-START-XQL-QUERY` | POST | `/public_api/v1/xql/start_xql_query` | ✅ read | `confirm-tail` |
