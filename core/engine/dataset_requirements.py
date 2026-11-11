"""Dataset requirement registry loader (``spec/dataset_requirements.yml``).

For every dataset the corpus names, the registry records what must be true on
the Cortex side (built-in, data source, pack, collector, broker applet) and on
the source side (what the producing product must be configured to emit), plus
which EAL plugins emulate it. Later tasks derive scenario prerequisites from it.

An ABSENT file returns ``None`` from :func:`load` (callers render that as
degraded, never as "no requirements"). A file that is PRESENT but malformed
raises :class:`DatasetRequirementsError`: an unrecognised shape has no safe
reading, and the tolerant one would keep the bug and green the test.

Importing this module reads nothing; the file is read on the first ``load()``.
No outbound calls.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from config import settings

logger = logging.getLogger("cortexsim.dataset_requirements")

PRODUCED_BY: frozenset[str] = frozenset({
    "cortex-xdr-agent", "cortex-xsiam", "cortex-cloud", "cortex-xpanse", "strata-ngfw",
    "prisma-browser", "prisma-airs", "idira", "okta", "microsoft-entra", "microsoft-365",
    "microsoft-windows", "google-workspace", "cloud-provider-audit", "kubernetes",
    "proofpoint", "cisco-asa", "ivanti", "koi", "saas-generic", "cortexsim-synthetic",
    "vmware-esxi",
})

CORTEX_SIDE_KINDS: frozenset[str] = frozenset({
    "built_in", "data_source", "marketplace_pack", "collector", "broker_applet",
})

_NGFW_REQUIREMENTS = frozenset({
    "log_forwarding", "threat_profile_attached", "url_filtering_logged",
    "dns_security_logged", "decryption_for_app_id", "enhanced_application_logs",
    "globalprotect_logs", "allow_rule_for_sim_traffic",
})
_GENERIC_REQUIREMENTS = frozenset({
    "audit_log_export", "api_integration", "agent_installed", "log_forwarding",
})
# Cortex Xpanse attributes findings only to assets inside the organisation's configured scope.
_XPANSE_REQUIREMENTS = _GENERIC_REQUIREMENTS | {"asset_scope_configured"}
SOURCE_REQUIREMENTS: dict[str, frozenset[str]] = {
    product: (_NGFW_REQUIREMENTS if product == "strata-ngfw"
              else _XPANSE_REQUIREMENTS if product == "cortex-xpanse"
              else _GENERIC_REQUIREMENTS)
    for product in sorted(PRODUCED_BY)
}


class DatasetRequirementsError(ValueError):
    """spec/dataset_requirements.yml is present but is not a registry this code can read."""


@dataclass(frozen=True)
class Verified:
    by: str
    on: str
    how: str

    @property
    def state(self) -> str:
        if self.by.strip() and self.on.strip() and self.how.strip():
            return "confirmed in a tenant"
        return "from documentation"


@dataclass(frozen=True)
class CortexSide:
    kind: str
    name: str | None
    verified: Verified


@dataclass(frozen=True)
class SourceSide:
    product: str
    requirement: str
    detail: str
    verified: Verified


@dataclass(frozen=True)
class DatasetRequirement:
    dataset: str
    produced_by: str
    cortex_side: tuple[CortexSide, ...]
    source_side: tuple[SourceSide, ...]
    emulated_by: tuple[str, ...]

    def to_dict(self) -> dict:
        def ver(v: Verified) -> dict:
            return {"by": v.by, "on": v.on, "how": v.how, "state": v.state}

        return {
            "dataset": self.dataset,
            "produced_by": self.produced_by,
            "cortex_side": [
                {"kind": c.kind, "name": c.name, "verified": ver(c.verified)}
                for c in self.cortex_side
            ],
            "source_side": [
                {"product": s.product, "requirement": s.requirement,
                 "detail": s.detail, "verified": ver(s.verified)}
                for s in self.source_side
            ],
            "emulated_by": list(self.emulated_by),
        }


def registry_path() -> str:
    return os.path.join(settings.CORTEXSIM_BASE_DIR, "spec", "dataset_requirements.yml")


def _bad(path: str, where: str, problem: str) -> DatasetRequirementsError:
    return DatasetRequirementsError(
        f"dataset requirement registry {path} is malformed at {where}: {problem}. "
        f"Fix or restore the file; a registry that exists but cannot be read has no safe default."
    )


def _keys(obj: object, where: str, path: str, required: set[str]) -> dict:
    if not isinstance(obj, dict):
        raise _bad(path, where, f"expected a mapping, got {type(obj).__name__}")
    _string_keys(obj, where, path)
    unknown = set(obj) - required
    if unknown:
        raise _bad(path, where, f"unknown key(s) {sorted(unknown, key=str)}")
    missing = required - set(obj)
    if missing:
        raise _bad(path, where, f"missing key(s) {sorted(missing)}")
    return obj


def _string_keys(obj: dict, where: str, path: str) -> None:
    for k in obj:
        if not isinstance(k, str):
            raise _bad(path, where, f"key {k!r} is not a string — quote it "
                                    f"(a bare `on:` is read as a boolean)")


def _text(val: object, where: str, path: str) -> str:
    if not isinstance(val, str) or not val.strip():
        raise _bad(path, where, f"must be a non-blank string, got {val!r}")
    return val


def _verified(obj: object, where: str, path: str) -> Verified:
    d = _keys(obj, where, path, {"by", "on", "how"})
    for k in ("by", "on", "how"):
        if not isinstance(d[k], str):
            raise _bad(path, f"{where}.{k}", f"must be a string (blank allowed), got {d[k]!r}")
    return Verified(d["by"], d["on"], d["how"])


def _list(obj: object, where: str, path: str) -> list:
    if not isinstance(obj, list):
        raise _bad(path, where, f"expected a list, got {type(obj).__name__}")
    return obj


def parse_registry(data: object, path: str) -> dict[str, DatasetRequirement]:
    if not isinstance(data, dict) or not data:
        raise _bad(path, "root", f"must be a non-empty mapping of dataset to entry, got {data!r}")
    _string_keys(data, "root", path)
    out: dict[str, DatasetRequirement] = {}
    for name, entry in data.items():
        w = f"{name}"
        _text(name, "dataset name", path)
        e = _keys(entry, w, path, {"produced_by", "cortex_side", "source_side", "emulated_by"})
        produced_by = _text(e["produced_by"], f"{w}.produced_by", path)
        if produced_by not in PRODUCED_BY:
            raise _bad(path, f"{w}.produced_by", f"{produced_by!r} is not one of {sorted(PRODUCED_BY)}")

        cortex_raw = _list(e["cortex_side"], f"{w}.cortex_side", path)
        if not cortex_raw:
            raise _bad(path, f"{w}.cortex_side", "must not be empty")
        cortex: list[CortexSide] = []
        for i, c in enumerate(cortex_raw):
            cw = f"{w}.cortex_side[{i}]"
            cd = _keys(c, cw, path, {"kind", "name", "verified"})
            kind = _text(cd["kind"], f"{cw}.kind", path)
            if kind not in CORTEX_SIDE_KINDS:
                raise _bad(path, f"{cw}.kind", f"{kind!r} is not one of {sorted(CORTEX_SIDE_KINDS)}")
            cname = cd["name"]
            if cname is not None:
                cname = _text(cname, f"{cw}.name", path)
            cortex.append(CortexSide(kind, cname, _verified(cd["verified"], f"{cw}.verified", path)))

        source: list[SourceSide] = []
        for i, s in enumerate(_list(e["source_side"], f"{w}.source_side", path)):
            sw = f"{w}.source_side[{i}]"
            sd = _keys(s, sw, path, {"product", "requirement", "detail", "verified"})
            product = _text(sd["product"], f"{sw}.product", path)
            if product not in PRODUCED_BY:
                raise _bad(path, f"{sw}.product", f"{product!r} is not one of {sorted(PRODUCED_BY)}")
            req = _text(sd["requirement"], f"{sw}.requirement", path)
            if req not in SOURCE_REQUIREMENTS[product]:
                raise _bad(path, f"{sw}.requirement",
                           f"{req!r} is not a requirement for {product}: "
                           f"{sorted(SOURCE_REQUIREMENTS[product])}")
            detail = _text(sd["detail"], f"{sw}.detail", path)
            source.append(SourceSide(product, req, detail, _verified(sd["verified"], f"{sw}.verified", path)))

        emulated = tuple(
            _text(x, f"{w}.emulated_by[{i}]", path)
            for i, x in enumerate(_list(e["emulated_by"], f"{w}.emulated_by", path))
        )
        out[name] = DatasetRequirement(name, produced_by, tuple(cortex), tuple(source), emulated)
    return out


@lru_cache(maxsize=1)
def load() -> dict[str, DatasetRequirement] | None:
    path = registry_path()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        logger.warning("dataset requirement registry not found at %s", path)
        return None
    except OSError as exc:
        raise _bad(path, "file", f"it is present but cannot be read ({exc})") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise _bad(path, "file", f"it is not valid YAML ({exc})") from exc
    return parse_registry(data, path)


# Detection kinds whose content names a dataset. ``xql_queries`` declare it in a ``dataset``
# field; the other three name it in ``logic`` through a ``dataset =`` / ``preset =`` anchor.
QUERY_KIND = "xql_queries"
LOGIC_KINDS: tuple[str, ...] = ("biocs", "abiocs", "correlation_rules")
DETECTION_KINDS: tuple[str, ...] = (QUERY_KIND, *LOGIC_KINDS)

# Same semantics as detection_scanner/scripts/validate.py ``_DATASET_SOURCE_RE``: the keyword
# matches case-insensitively and the name is lowercased. Unlike validate.py (which lints only
# the first anchor), every anchor is read: a union or join sub-source is a dataset too.
_ANCHOR = re.compile(r"(?:preset|dataset)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


@dataclass(frozen=True)
class DatasetRead:
    """One place a card names a dataset. ``dataset`` is ``None`` only for an xql query that
    declares none (a missing, ``None``, blank or whitespace-only ``dataset`` field)."""
    kind: str
    label: str
    dataset: str | None


def walk_card(card_id: str, raw: object) -> list[DatasetRead]:
    """Every dataset one card reads, in kind order then item order. The single implementation.

    One ``DatasetRead`` per xql query (dataset or ``None``) and one per anchor in a BIOC, ABIOC
    or correlation ``logic``. An unrecognised shape (card or ``detections`` not a mapping, a kind
    not a list, an item not a mapping, a non-string ``dataset``, a missing or non-string
    ``logic``) raises :class:`DatasetRequirementsError`: skipping it would drop that card's
    prerequisites without a trace.
    """
    if not isinstance(raw, dict):
        raise DatasetRequirementsError(f"card {card_id} must be a mapping, got {type(raw).__name__}")
    detections = raw.get("detections")
    if detections is None:
        return []
    if not isinstance(detections, dict):
        raise DatasetRequirementsError(
            f"card {card_id}: detections must be a mapping, got {type(detections).__name__}")
    out: list[DatasetRead] = []
    for kind in DETECTION_KINDS:
        items = detections.get(kind)
        if items is None:
            continue
        if not isinstance(items, list):
            raise DatasetRequirementsError(
                f"card {card_id}: detections.{kind} must be a list, got {type(items).__name__}")
        for i, item in enumerate(items):
            where = f"card {card_id}: detections.{kind}[{i}]"
            if not isinstance(item, dict):
                raise DatasetRequirementsError(f"{where} must be a mapping, got {type(item).__name__}")
            name = item.get("name")
            label = name if isinstance(name, str) and name.strip() else f"#{i}"
            if kind == QUERY_KIND:
                ds = item.get("dataset")
                if ds is not None and not isinstance(ds, str):
                    raise DatasetRequirementsError(
                        f"{where}.dataset must be a string, got {type(ds).__name__}")
                out.append(DatasetRead(kind, label, ds.strip() if ds and ds.strip() else None))
                continue
            logic = item.get("logic")
            if not isinstance(logic, str):
                raise DatasetRequirementsError(
                    f"{where}.logic must be a string, got {type(logic).__name__}")
            out.extend(DatasetRead(kind, label, m.group(1).lower()) for m in _ANCHOR.finditer(logic))
    return out


def card_dataset_index(all_raw: dict[str, dict]) -> dict[str, dict[str, set[str]]]:
    """``{dataset: {card id: {detection kinds}}}`` over ``catalog.all_raw()``, via :func:`walk_card`."""
    out: dict[str, dict[str, set[str]]] = {}
    for card_id, raw in all_raw.items():
        for read in walk_card(card_id, raw):
            if read.dataset is not None:
                out.setdefault(read.dataset, {}).setdefault(card_id, set()).add(read.kind)
    return out


def card_datasets(all_raw: dict[str, dict]) -> dict[str, set[str]]:
    """Map each dataset any card reads (see :func:`walk_card`) to the card ids that read it."""
    return {ds: set(cards) for ds, cards in card_dataset_index(all_raw).items()}


def registry_source() -> dict[str, str]:
    """``{"path", "source"}``; source is ``"file"`` or ``"absent"``, read once per process like ``load()``."""
    return {"path": registry_path(), "source": "absent" if load() is None else "file"}


def check_datasets_registered(card_ds: dict[str, set[str]],
                              registry: dict[str, DatasetRequirement] | None) -> list[str]:
    """Sorted names of card datasets with no registry entry (all of them when ``registry`` is ``None``).

    Raises :class:`DatasetRequirementsError` when ``CORTEXSIM_STRICT_DATASETS`` is on and the
    list is non-empty, so a corpus dataset nobody documented cannot boot silently.
    """
    unregistered = sorted(name for name in card_ds if registry is None or name not in registry)
    if unregistered and settings.CORTEXSIM_STRICT_DATASETS:
        raise DatasetRequirementsError(
            f"{len(unregistered)} card dataset(s) have no entry in {registry_path()}: "
            f"{', '.join(unregistered)}. Add them to the registry, or unset "
            f"CORTEXSIM_STRICT_DATASETS to boot with a warning."
        )
    return unregistered


def boot_check_dataset_requirements(all_raw: dict[str, dict]) -> list[str]:
    """Lifespan check: the registry against the datasets the cards read. Returns the unregistered names.

    With ``CORTEXSIM_STRICT_DATASETS`` on, a malformed registry, a malformed card shape or an
    unregistered dataset raises. With it off, none of them stops boot: a malformed registry or
    card shape is logged at error level with its path, an unregistered dataset at warning level,
    and /api/health plus the routes report the same fault.
    """
    try:
        registry = load()
        unregistered = check_datasets_registered(card_datasets(all_raw), registry)
    except DatasetRequirementsError as exc:
        if settings.CORTEXSIM_STRICT_DATASETS:
            raise
        logger.error("dataset requirements check failed (registry %s): %s; continuing because "
                     "CORTEXSIM_STRICT_DATASETS is off", registry_path(), exc)
        return []
    if unregistered:
        logger.warning("dataset requirement registry %s: %d card dataset(s) unregistered: %s",
                       "absent" if registry is None else "loaded", len(unregistered),
                       ", ".join(unregistered))
    return unregistered
