"""
CortexSim UC/TC Registry — the master Use-Case / Test-Case index as a read path.

Loads the versioned v2.2 snapshot under ``docs/uc_tc_mapping/_v2.2-source/``
into frozen dataclasses so the scenario loader can validate ``uc_ref`` /
``tc_ref`` as a real foreign key instead of trusting free text.

Namespace decision (see docs/uc_tc_mapping/README.md):

* **B — the master index — is canonical.** That is what this module loads.
* **A — scenario ``uc_ref``/``tc_ref``** — becomes a validated FK into B.
* **C — TTP-card ``panw_mapping``** — is a card-local threat-narrative label
  and is deliberately *not* joined here.

Design rules (mirrors ``ttp_catalog.py`` exactly):

* Pure read path. The registry never mutates the CSV snapshot.
* Fail soft. A missing or malformed snapshot logs a warning and yields an
  empty registry — startup does not abort. Ref validation degrades to
  advisory when the registry is empty (see ``loaded``).
* No ORM table. The index is a versioned file artifact, not runtime state.

Process-level singleton refreshed at boot and re-loadable for tests.
"""

from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("cortexsim.uctc_registry")


# ---------------------------------------------------------------------------
# Index dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestCase:
    """One row of the master TC index, fused with its detection-spec row."""

    tc_id: str
    ucs_id: str
    uc_id: str
    use_case: str
    scenario: str
    title: str
    description: str

    # measurement contract
    validation_methodology: str
    primary_kpi: str
    threshold: str
    measurement_method: str
    success_criteria: str
    detection_source: str
    expected_signal: str
    simulation_input: str

    # positioning
    moat_classification: str
    differentiation_tier: str          # MOAT | LEAD | EMERGING | PARITY
    tc_sheet: str
    status: str

    # from detection_spec_v2.2.csv (may be blank if the row is absent)
    validation_class: str = ""         # DET | HNT | POS | PLT | AUT
    priority: str = ""                 # P1 | P2 | P3
    detection_id: str = ""
    detection_type: str = ""
    target_dataset: str = ""
    pov_scenario_id: str = ""
    base_platform: str = ""
    required_addon: str = ""
    authoring_gap: str = ""

    @property
    def is_scoreable(self) -> bool:
        """False when the index carries no measurable threshold — the verifier
        must emit ``not_applicable`` for these rather than a silent pass."""
        return self.threshold not in ("", "Qualitative pass", "TBD")

    @property
    def needs_detection_content(self) -> bool:
        """DET/HNT rows need authored detection content; POS/PLT/AUT rows are
        assertions against platform or posture state, not detections."""
        return self.validation_class in ("DET", "HNT")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tc_id": self.tc_id,
            "ucs_id": self.ucs_id,
            "uc_id": self.uc_id,
            "use_case": self.use_case,
            "scenario": self.scenario,
            "title": self.title,
            "description": self.description,
            "validation_methodology": self.validation_methodology,
            "primary_kpi": self.primary_kpi,
            "threshold": self.threshold,
            "measurement_method": self.measurement_method,
            "success_criteria": self.success_criteria,
            "detection_source": self.detection_source,
            "expected_signal": self.expected_signal,
            "moat_classification": self.moat_classification,
            "differentiation_tier": self.differentiation_tier,
            "validation_class": self.validation_class,
            "priority": self.priority,
            "base_platform": self.base_platform,
            "required_addon": self.required_addon,
            "is_scoreable": self.is_scoreable,
        }


@dataclass(frozen=True)
class UseCase:
    """One row of the master UC index."""

    uc_id: str
    use_case: str
    domain: str
    fy27_subdomain: str
    value_drivers: str
    scenario_count: int
    test_case_count: int
    products: str
    product_addon: str
    status: str

    # from product_addon_v2.2.csv — the license contract (Phase 3)
    base_platform: str = ""
    addon: str = ""
    addon_skus: str = ""
    min_license_path: str = ""
    bundle_alternative: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uc_id": self.uc_id,
            "use_case": self.use_case,
            "domain": self.domain,
            "fy27_subdomain": self.fy27_subdomain,
            "value_drivers": self.value_drivers,
            "products": self.products,
            "status": self.status,
            "base_platform": self.base_platform,
            "addon": self.addon,
            "addon_skus": self.addon_skus,
            "min_license_path": self.min_license_path,
            "bundle_alternative": self.bundle_alternative,
        }


@dataclass(frozen=True)
class Sku:
    """One capability → part-number row of the FY27 price book."""

    capability: str
    sku_part_number: str
    attaches_to: str
    meter: str
    note: str


@dataclass(frozen=True)
class RefVerdict:
    """Result of validating one scenario's ``(uc_ref, tc_ref)`` pair.

    ``status`` is one of:

    * ``ok``            — tc_ref resolves and uc_ref agrees with its parent
    * ``dangling_tc``   — no such tc_id in the index            (S-10)
    * ``dangling_uc``   — tc_ref resolves, uc_ref is not a known UCS/UC (S-11)
    * ``fk_mismatch``   — both resolve but tc_ref's parent ≠ uc_ref     (S-12)
    * ``unverified``    — the registry is empty (snapshot missing); advisory
    """

    status: str
    uc_ref: str
    tc_ref: str
    detail: str = ""
    test_case: Optional[TestCase] = None

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "unverified")

    @property
    def is_error(self) -> bool:
        """S-10/S-11/S-12 are the hard-error codes gated by strict mode."""
        return self.status in ("dangling_tc", "dangling_uc", "fk_mismatch")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class UcTcRegistry:
    """In-memory view of the master UC/TC index.

    Lookups are by ``tc_id`` / ``ucs_id`` / ``uc_id``. All three namespaces
    are flat and globally unique in the v2.2 export.
    """

    def __init__(self) -> None:
        self._tc: dict[str, TestCase] = {}
        self._uc: dict[str, UseCase] = {}
        self._by_ucs: dict[str, list[TestCase]] = {}
        self._sku: dict[str, Sku] = {}
        self._version: str = ""

    # ---- lifecycle -----------------------------------------------------

    def load(self, source_dir: str) -> int:
        """Read the v2.2 CSV snapshot from ``source_dir`` and replace the
        registry. Returns the number of test cases indexed."""
        self._tc.clear()
        self._uc.clear()
        self._by_ucs.clear()
        self._sku.clear()
        self._version = ""

        if not os.path.isdir(source_dir):
            logger.warning(
                "UC/TC index snapshot not found at %s — registry will be empty "
                "and ref validation degrades to advisory",
                source_dir,
            )
            return 0

        self._version = _read_version(os.path.dirname(source_dir.rstrip(os.sep)))

        # detection_spec rows enrich the TC rows; load them first.
        spec = {
            r["tc_id"]: r
            for r in _read_csv(os.path.join(source_dir, "detection_spec_v2.2.csv"))
        }
        addon = {
            r["uc_id"]: r
            for r in _read_csv(os.path.join(source_dir, "product_addon_v2.2.csv"))
        }

        for row in _read_csv(os.path.join(source_dir, "tc_index_v2.2.csv")):
            tc_id = (row.get("tc_id") or "").strip()
            if not tc_id:
                continue
            if tc_id in self._tc:
                logger.warning(
                    "UC/TC index carries duplicate tc_id=%s — keeping first row", tc_id
                )
                continue
            s = spec.get(tc_id, {})
            tc = TestCase(
                tc_id=tc_id,
                ucs_id=(row.get("ucs_id") or "").strip(),
                uc_id=(row.get("uc_id") or "").strip(),
                use_case=row.get("use_case", ""),
                scenario=row.get("scenario", ""),
                title=row.get("test_case_title", ""),
                description=row.get("test_case_description", ""),
                validation_methodology=row.get("validation_methodology", ""),
                primary_kpi=row.get("primary_kpi", ""),
                threshold=row.get("threshold", ""),
                measurement_method=row.get("measurement_method", ""),
                success_criteria=row.get("success_criteria", ""),
                detection_source=row.get("detection_source", ""),
                expected_signal=row.get("expected_signal", ""),
                simulation_input=row.get("simulation_input", ""),
                moat_classification=row.get("moat_classification", ""),
                differentiation_tier=(row.get("differentiation_tier") or "").strip(),
                tc_sheet=row.get("tc_sheet", ""),
                status=row.get("status", ""),
                validation_class=s.get("validation_class", ""),
                priority=s.get("priority", ""),
                detection_id=s.get("detection_id", ""),
                detection_type=s.get("detection_type", ""),
                target_dataset=s.get("target_dataset", ""),
                pov_scenario_id=s.get("pov_scenario_id", ""),
                base_platform=s.get("base_platform", ""),
                required_addon=s.get("required_addon", ""),
                authoring_gap=s.get("authoring_gap", ""),
            )
            self._tc[tc_id] = tc
            self._by_ucs.setdefault(tc.ucs_id, []).append(tc)

        for row in _read_csv(os.path.join(source_dir, "uc_index_v2.2.csv")):
            uc_id = (row.get("uc_id") or "").strip()
            if not uc_id:
                continue
            a = addon.get(uc_id, {})
            self._uc[uc_id] = UseCase(
                uc_id=uc_id,
                use_case=row.get("use_case", ""),
                domain=row.get("domain", ""),
                fy27_subdomain=row.get("fy27_subdomain", ""),
                value_drivers=row.get("value_drivers", ""),
                scenario_count=_int(row.get("scenario_count")),
                test_case_count=_int(row.get("test_case_count")),
                products=row.get("products", ""),
                product_addon=row.get("product_addon", ""),
                status=row.get("status", ""),
                base_platform=a.get("base_platform", ""),
                addon=a.get("addon", ""),
                addon_skus=a.get("addon_skus", ""),
                min_license_path=a.get("min_license_path", ""),
                bundle_alternative=a.get("bundle_alternative", ""),
            )

        for row in _read_csv(os.path.join(source_dir, "sku_catalog.csv")):
            cap = (row.get("capability") or "").strip()
            if not cap:
                continue
            self._sku[cap] = Sku(
                capability=cap,
                sku_part_number=row.get("sku_part_number", ""),
                attaches_to=row.get("attaches_to", ""),
                meter=row.get("meter", ""),
                note=row.get("note", ""),
            )

        logger.info(
            "UC/TC registry loaded (v%s): %d test cases across %d use cases "
            "(%d UCS groups, %d SKUs) from %s",
            self._version or "?", len(self._tc), len(self._uc),
            len(self._by_ucs), len(self._sku), source_dir,
        )
        return len(self._tc)

    @property
    def loaded(self) -> bool:
        """True when a snapshot is present. Ref validation is only meaningful
        against a loaded registry — an empty one returns ``unverified``."""
        return bool(self._tc)

    @property
    def version(self) -> str:
        return self._version

    # ---- lookups -------------------------------------------------------

    def tc(self, tc_id: Optional[str]) -> Optional[TestCase]:
        return self._tc.get(tc_id) if tc_id else None

    def ucs(self, ucs_id: Optional[str]) -> list[TestCase]:
        """All test cases under one use-case scenario group."""
        return list(self._by_ucs.get(ucs_id, [])) if ucs_id else []

    def uc(self, uc_id: Optional[str]) -> Optional[UseCase]:
        return self._uc.get(uc_id) if uc_id else None

    def sku(self, capability: Optional[str]) -> Optional[Sku]:
        return self._sku.get(capability) if capability else None

    def all_test_cases(self) -> list[TestCase]:
        return list(self._tc.values())

    def all_use_cases(self) -> list[UseCase]:
        return list(self._uc.values())

    def all_skus(self) -> list[Sku]:
        return list(self._sku.values())

    def capabilities(self) -> set[str]:
        """Valid ``required_addons`` values for Phase-3 enum validation."""
        return set(self._sku)

    def known_uc_refs(self) -> set[str]:
        """Every id a scenario's ``uc_ref`` may legally carry — the index
        exposes both the UCS (scenario-group) and UC (use-case) namespaces,
        and scenarios in the corpus use both forms."""
        return set(self._by_ucs) | set(self._uc)

    def unscoreable(self) -> list[TestCase]:
        """DET/HNT test cases the index cannot score — no measurable threshold.
        Surfaced so the verifier emits ``not_applicable`` instead of a pass."""
        return [
            tc for tc in self._tc.values()
            if tc.needs_detection_content and not tc.is_scoreable
        ]

    # ---- validation ----------------------------------------------------

    def validate_ref(self, uc_ref: str, tc_ref: str) -> RefVerdict:
        """Validate a scenario's ``(uc_ref, tc_ref)`` pair against the index.

        ``uc_ref`` may be either a UCS id (``UCS-EDR-01``) or a UC id
        (``UC-EDR``); both are legal FKs and the corpus uses both.
        """
        if not self.loaded:
            return RefVerdict("unverified", uc_ref, tc_ref,
                              "UC/TC index snapshot not loaded")

        tc = self._tc.get(tc_ref)
        if tc is None:
            return RefVerdict(
                "dangling_tc", uc_ref, tc_ref,
                f"tc_ref '{tc_ref}' does not exist in the v{self._version} index",
            )

        if uc_ref not in self.known_uc_refs():
            return RefVerdict(
                "dangling_uc", uc_ref, tc_ref,
                f"uc_ref '{uc_ref}' is neither a known UCS group nor a known UC "
                f"in the v{self._version} index",
                test_case=tc,
            )

        if uc_ref not in (tc.ucs_id, tc.uc_id):
            return RefVerdict(
                "fk_mismatch", uc_ref, tc_ref,
                f"tc_ref '{tc_ref}' belongs to {tc.ucs_id} / {tc.uc_id}, "
                f"but uc_ref declares '{uc_ref}'",
                test_case=tc,
            )

        return RefVerdict("ok", uc_ref, tc_ref, "", test_case=tc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_csv(path: str) -> list[dict[str, str]]:
    """Read a snapshot CSV. Missing/unreadable files are a warning, not a raise."""
    if not os.path.isfile(path):
        logger.warning("UC/TC index snapshot missing %s — skipping", path)
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error) as exc:
        logger.warning("UC/TC index snapshot unreadable %s: %s", path, exc)
        return []


def _read_version(mapping_dir: str) -> str:
    """First line of ``docs/uc_tc_mapping/VERSION``, or '' when absent."""
    path = os.path.join(mapping_dir, "VERSION")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.readline().strip()
    except OSError:
        return ""


def _int(v: Any) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------


registry = UcTcRegistry()


def default_index_dir(base_dir: str) -> str:
    """Convention: ``<base>/docs/uc_tc_mapping/_v2.2-source``."""
    return os.path.join(base_dir, "docs", "uc_tc_mapping", "_v2.2-source")
