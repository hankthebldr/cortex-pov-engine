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
SOURCE_REQUIREMENTS: dict[str, frozenset[str]] = {
    product: (_NGFW_REQUIREMENTS if product == "strata-ngfw" else _GENERIC_REQUIREMENTS)
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
    unknown = set(obj) - required
    if unknown:
        raise _bad(path, where, f"unknown key(s) {sorted(unknown, key=str)}")
    missing = required - set(obj)
    if missing:
        raise _bad(path, where, f"missing key(s) {sorted(missing)}")
    return obj


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
            kind = cd["kind"]
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


def registry_source() -> dict[str, str]:
    """``{"path", "source"}``; source is ``"file"`` or ``"absent"``."""
    path = registry_path()
    return {"path": path, "source": "file" if os.path.isfile(path) else "absent"}
