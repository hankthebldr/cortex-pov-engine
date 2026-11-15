# socfw fixtures: provenance

Pinned to `soc-optimization-unified` 3.31.4, secops-framework commit `d222ba87`.
No tenant was contacted. Nothing here is a tenant recording.

| File | Source |
|---|---|
| `config_modes.json` | Byte copy of `Packs/soc-optimization-unified/Lists/SOCFWConfigModes/SOCFWConfigModes_data.json`. |
| `tenant_config.json` | Derived (ruling 3). The framework tests (`SOCFWConfig_test.py`) hold no pov-mode `SOCFWTenantConfig` document, so this is what `SOCFWConfig.py::_apply_mode_shadow({}, modes, "pov")` writes for `command=mode mode=pov`: a `shadow` section equal to the `pov` entry's `shadow` map. The mode name is not stored; it is inferred by `matching_mode()`. No `triage` section (a mode never changes triage; `command=triage` writes `{"triage": {"score_threshold": n or "off", "window_hours": n}}`, `SOCFWConfig.py` module docstring and `triage_value()`). |
| `triage_resolve_comment.txt` | Line 1: version. Line 2: `RESOLVE_COMMENT` from `Scripts/SOCAutoTriageScoreFilter/SOCAutoTriageScoreFilter.py:18-20` (implicit string concatenation joined verbatim). |
| `installed_packs.json` | Shape from povkit `internal/tenant/api.go` `InstalledPack` (`id`, `name`, `currentVersion`) for `GET /xsoar/contentpacks/metadata/installed`. Values are authored for the test: core pack at 3.31.4 (`pack_metadata.json` `currentVersion`), plus two other pack ids. |
| `list_names.json` | Shape from povkit `ListNames` (`GET /lists/names`, a JSON array of strings). Names are those the framework code uses (`SOCFWTenantConfig`, `SOCFWConfigModes`, `SOCFrameworkActions_V3`, `SOCOptimizationConfig_V3`). |
