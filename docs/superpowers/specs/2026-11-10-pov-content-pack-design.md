# POV content pack: what the tenant and its neighbours need before a run (2026-11-10)

Status: **proposed, awaiting approval.** Nothing is built. Branch
`docs/ttp-pov-content-pack`, cut from `dev` @ ec4471c.

Sibling spec: `2026-11-10-composer-correlation-story-design.md` (branch
`feature/ui-composer-correlation-story`, 4205fb2, also awaiting approval). That
spec owns the correlation-rule `deploy` block, its validator (V1 to V4) and the
`/tenant-content` manifest. This spec does not redefine any of it. It supplies
the format anchor that spec is gated on (§4) and widens the manifest to
everything else a run depends on (§3, §5 to §8).

## 1. Problem

A scenario declares what Cortex should detect. It does not say what has to be
true of the tenant, and of the products feeding it, for that detection to be
possible. A DC finds out mid-POV: the dataset is empty, the rule does not
exist, the firewall never forwarded the log. In a POV report each of those
reads as "Cortex missed it".

Measured on `main` @ 1ad8a88 (191 cards; recount on `dev` in phase 1):

| What a run depends on | In the corpus today |
|---|---|
| XQL the DC runs | 626 queries with real text (522 validation, 95 hunt, 7 metric, 2 investigation) |
| Correlation rules | 209, all `{name, rule_id, logic}`; 0 deployable |
| BIOC rules | 365, `logic` text; none in an importable shape |
| IOCs | 410; 101 carry a published source and a safe form |
| Dashboards, widgets, reports | 0 |
| Datasets the queries read | 31 distinct. `xdr_data` 83 cards, `panw_ngfw_traffic_raw` 53, `cloud_audit_logs` 33, then a long tail (`okta_sso`, `msft_o365_audit`, `proofpoint_tap_raw`, ...) |
| What must be installed or configured for a dataset to exist | nothing recorded |
| Firewall, identity-platform or marketplace prerequisites | nothing recorded |

The last two rows are the gap this spec closes. The first five are content the
DC loads; the rest is configuration someone must already have done.

## 2. The model: five layers, one manifest

```
  scenario ──▶ cards ──▶ detections ──▶ datasets
                              │              │
                              ▼              ▼
   L1 tenant detection content        L3 ingestion prerequisites
      (correlation, BIOC, indicators)     (marketplace items, collectors)
   L2 tenant presentation content     L4 adjacent platform configuration
      (dashboard, query library)          (Strata NGFW, Idira)
                                      L5 exposure expectations
                                          (planted finding ▶ managed rule)
```

| Layer | The DC's action | CortexSim ships | Scored? |
|---|---|---|---|
| L1 | import into the tenant | rule and indicator files | yes, by the existing run |
| L2 | import into the tenant | dashboard and query files | no; presentation only |
| L3 | install and configure | a checklist with the dataset each item produces | no; gates the run |
| L4 | configure another product | a checklist, and config snippets where a format is verified | no; gates the run |
| L5 | nothing to load | the expected-findings list per IaC module | by POS assertions |

All five are served as sections of the sibling spec's manifest
(`GET /api/scenarios/{id}/tenant-content`). One manifest, so a DC has one place
to read "what must be true before I launch".

**No write path.** Every item is a file or a checklist the DC acts on.
SimCore calls no insert endpoint; `CORTEXSIM_XSIAM_ALLOW_WRITE` stays off.

## 3. Requirements are derived from datasets, never authored per scenario

Authoring prerequisites on 193 scenarios would drift within a release. The
dependency already exists in data: a scenario cites cards, a card's queries
name datasets, and a dataset exists only if a specific source is onboarded.

So the one hand-authored artifact is a **dataset requirement registry**,
`spec/dataset_requirements.yml`, one entry per dataset (31 today):

```yaml
panw_ngfw_traffic_raw:
  produced_by: strata-ngfw            # closed vocabulary of sources
  cortex_side:                        # L3
    - kind: data_source               # data_source | marketplace_pack | collector | broker_applet
      name: ""                        # exact name as the tenant shows it
      verified: {by: "", on: "", how: ""}   # empty until checked in a tenant
  source_side:                        # L4
    - product: strata-ngfw
      requirement: log_forwarding     # closed vocabulary per product
      detail: "Traffic logs forwarded to the Cortex tenant"
      verified: {by: "", on: "", how: ""}
  emulated_by: [ngfw_eal_emitter]     # EAL plugin that can stand in for the source
```

* A scenario's requirements are **derived at load**, the way
  `required_base_platform` is derived from the UC/TC registry. No scenario YAML
  changes.
* `emulated_by` is what makes the checklist honest in a lab: when an EAL
  emitter posts the records, the source-side row is reported as "emulated, not
  configured", never as satisfied.
* A dataset a card queries that has no registry entry **raises at load** under
  a strict flag and is listed as `UNREGISTERED_DATASET` otherwise. It is never
  treated as "no prerequisite".
* `verified` is per row and starts empty. An unverified row is printed as
  "from documentation, not confirmed in a tenant".

## 4. L1: tenant detection content

**Correlation rules.** Owned by the sibling spec. Its gate was a field list for
a rule a tenant accepts. One public anchor exists: the Cortex XDR integration's
`xdr-correlation-rule-create` command (xsoar.pan.dev, Cortex XDR - IR
reference, read 2026-11-10):

| Argument | Required | Values |
|---|---|---|
| `name`, `xql_query`, `dataset`, `timezone` | yes | |
| `severity` | yes | info, low, medium, high, critical |
| `is_enabled` | yes | true, false |
| `execution_mode` | yes | scheduled, real_time |
| `mapping_strategy` | yes | auto, custom |
| `alert_category` | yes | a 16-value enum (persistence, evasion, credential_access, ...) |
| `search_window`, `schedule`, `schedule_linux` | no | e.g. "1 hours", "10 minutes", a cron line |
| `suppression_enabled`, `suppression_duration`, `suppression_fields` | no | |
| `alert_name`, `alert_description`, `alert_fields` | no | |
| `mitre_defs_json`, `user_defined_severity`, `user_defined_category` | no | |
| `investigation_query_link`, `drilldown_query_timeframe` | no | |

This is an integration's argument list, which wraps the API. It is strong
evidence for names and enums and it is not a tenant export. The sibling spec's
`deploy` block should adopt these names (`xql_query`, `execution_mode:
real_time`, `alert_category`, `mapping_strategy`, `dataset`, `timezone` are
all absent or spelled differently there). The gate closes fully when one rule
exported from a real tenant matches.

**BIOC rules.** Same page, `xdr-bioc-create`: `name`, `severity`, `indicator`
(required); `type` (the same 16-value enum), `is_xql`, `status`, `comment`,
MITRE tactic and technique (optional). The shape of `indicator` is not
documented there. BIOC export waits for a tenant-exported sample. Until then
BIOCs appear in the manifest as intent only.

**Indicators (XTI).** XTI is Extended Threat Intelligence; it accepts
customer-managed indicators and raises issues from indicator rules. Phase 2
emits the IOCs that carry `published` and `safe_form` (101 today) as one file
per scenario in the `insert_jsons` shape already documented in
`docs/reference/xsiam-platform-apis/10-threat-intel-iocs-api.md`. The other
309 are listed and not emitted. Indicator-rule fields are unverified and out
of scope.

## 5. L2: tenant presentation content

* **Query library pack.** The 95 `hunt` and 2 `investigation` queries, grouped
  by use case and test case, one file per scenario plus one for the corpus.
  Phase 1 ships them as `.xql` text with a header (name, purpose, dataset,
  card). A library-import format is unverified; text the DC pastes is honest.
* **POV scoreboard dashboard.** One definition, parameterised by run window
  and scenario marker: alerts by scenario, technique coverage, time to first
  alert per step, dataset freshness for each registered dataset, prevented vs
  detected. Phase 3, gated on a dashboard exported from a real tenant. Until
  then the same content ships as the widget queries alone.

The dashboard reads the tenant. It is the customer's view of the run and it is
not evidence: `tenant-verified` moves only on a reconciled run.

## 6. L3: ingestion prerequisites and marketplace items

Each registry row names what the tenant needs for the dataset to fill: a
built-in data source, a marketplace content pack (which may carry parsing and
modeling rules, the dataset's schema and its own dashboards), a collector or a
Broker VM applet.

* Names are recorded exactly as the tenant shows them, with `verified`.
* The manifest groups them per scenario and de-duplicates across a POV scope
  (`POST /api/pov/scope` already defines the scenario set).
* Readiness gains a row per required dataset. Phase 4 can answer it with the
  existing opt-in dataset-presence probe in connector preflight; this spec adds
  no new outbound call.

## 7. L4: adjacent platform configuration

A closed requirement vocabulary per product. A requirement is a condition, not
a config dump.

**Strata NGFW** (71 of 191 cards on `dev` @ ec4471c read a `panw_ngfw_*` or `pan_*` dataset):

| Requirement | Needed when |
|---|---|
| `log_forwarding` | any NGFW dataset |
| `threat_profile_attached` | a card reads `panw_ngfw_threat_raw` |
| `url_filtering_logged`, `dns_security_logged` | the card's query filters on those log subtypes |
| `decryption_for_app_id` | the detection keys on an application inside TLS |
| `enhanced_application_logs` | the detection is an analytics detection over network data |
| `globalprotect_logs` | a card reads `panw_ngfw_globalprotect_raw` |
| `allow_rule_for_sim_traffic` | a live-traffic step must reach its destination to be observed |

The last row matters for honesty in the other direction: a firewall that
blocks the simulated traffic produces a prevention signal and no session, and
the run must not read that as a miss.

**Idira.** Idira is the Palo Alto Networks identity security platform built
on CyberArk (announced May 2026); the announcement says Cortex will receive
first-party identity signals from it. No dataset name, schema or onboarding
step is public as of 2026-11-10. The registry reserves `idira` as a source
with no rows. The 20 ITDR scenarios keep reading `okta_sso` and the Microsoft
datasets. Nothing is authored against Idira until a dataset can be named.

## 8. L5: exposure expectations

Attack surface rules are managed by Palo Alto Networks. A customer can enable
or disable a rule and change its severity (Cortex Cloud documentation, read
2026-11-10); there is no custom rule to ship. The asset is an
`expected_findings` list per IaC module (`asm`, `cspm`, `ai-spm`): planted
resource, the managed rule expected to fire on it, and its tag
(`CortexSimCSPMFinding=<type>` exists already). POS assertions consume the
list. Managed-rule names carry the same `verified` field.

## 9. Honesty rules

* **Three states per row, in words:** `confirmed in a tenant`, `from
  documentation`, `emulated`. No fourth state and no colour-only signal.
* **A missing registry entry is degraded.** It never renders as "nothing
  required".
* **Unverified formats ship as text, not as importable files.** A file that a
  tenant rejects on import costs a DC more than a query to paste.
* **Nothing here moves `tenant-verified`.** It is 0 and this spec does not
  change it.

## 10. Phasing

| Phase | Builds | Proves | Does not prove |
|---|---|---|---|
| **1 · Registry** | `spec/dataset_requirements.yml` for all datasets, loader, derivation, manifest sections L3 and L4, query library text export, the load-time guard | every dataset a card reads has a stated requirement, or is flagged | that any requirement text is right in a tenant |
| **2 · Indicators and names** | indicator files, correlation field names aligned with §4 in the sibling spec, expected-findings lists | files match the documented shapes | that a tenant imports them |
| **3 · Anchored formats** | from tenant exports: correlation `deploy` authoring (sibling phase 3), BIOC export, dashboard | content matches a real export | that it fires |
| **4 · Readiness** | per-dataset and per-rule presence rows on Readiness, read-only | the prerequisite exists in that tenant | that the run detects anything |

## 11. Guards for phase 1

* pytest: every dataset named by a card query resolves in the registry; an
  unknown dataset raises under strict and is listed otherwise; a registry row
  with an unknown `produced_by` or `requirement` does not load.
* pytest: derivation is pure; a scenario with no card returns
  `NO_CARD_BINDING`, never an empty requirement list.
* A drift guard pins the registry's dataset set to `validate.py`
  `KNOWN_DATASETS`, so the two cannot diverge.
* Each guard is observed failing with its change reverted.

## 12. Decisions owed

1. **Tenant exports.** Phase 3 needs one exported correlation rule, one BIOC
   and one dashboard from a real tenant (deathstar would do). Without them
   those three stay text.
2. **Who verifies registry rows.** 31 datasets need a named DC to confirm the
   tenant-side name once. Recommended: seed from documentation in phase 1,
   verify in the lab as scenarios are run.
3. **Strata snippets.** Ship conditions only (recommended), or also PAN-OS
   config fragments? Fragments go stale per PAN-OS release and imply a
   correctness the repo cannot test.
4. **Merge order.** The sibling spec should be amended with §4's field names
   before either is built.

## 13. Not done

* Writing XQL for the 209 correlation rules (sibling spec, phase 3).
* XSOAR playbooks. The 41 exported stubs are untouched.
* Any insert call to a tenant or a firewall.
* Idira content.
