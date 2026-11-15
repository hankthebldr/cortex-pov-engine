"""Route verdict for an HTTP log collector.

A consultant names a collector ``<vendor>`` / ``<product>`` in the tenant and the
tenant writes its records to ``<vendor>_<product>_raw``. The question this module
answers, with no I/O, is whether that is the dataset the library's detections read.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

# Datasets a native tenant pipe (the firewall's own log forwarding) fills. A custom
# collector named into one would mix synthetic records into real firewall data.
NATIVE_DATASETS = frozenset({
    "panw_ngfw_traffic_raw",
    "panw_ngfw_threat_raw",
    "panw_ngfw_globalprotect_raw",
})

TEST_CHANNEL = "test_channel"
TEST_CHANNEL_VENDOR = "cortexsim"
TEST_CHANNEL_PRODUCT = "door_http"

# Detection kinds that do not fire on a custom collector's dataset without an XDM rule.
LIMITED_KINDS = frozenset({"biocs", "abiocs"})


def _canonical(name: str) -> str:
    """Lowercase, runs of non-alphanumerics as one ``_``, edge ``_`` trimmed."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def dataset_for(vendor: str, product: str) -> str:
    """The dataset a collector named ``vendor`` / ``product`` writes, as typed."""
    return f"{vendor}_{product}_raw"


def prefill(source_key: str, card_dataset: Optional[str]) -> tuple[str, str]:
    """Suggested ``(vendor, product)`` for a source.

    A ``_raw`` card dataset is split at its first underscore after stripping ``_raw``;
    otherwise the source key is. A name with no underscore gives product ``logs``.
    """
    if card_dataset and card_dataset.endswith("_raw"):
        base = card_dataset[: -len("_raw")]
    else:
        base = source_key
    vendor, sep, product = base.partition("_")
    return (vendor, product) if sep and product else (base, "logs")


@dataclass(frozen=True)
class RouteVerdict:
    verdict: str
    dataset: str
    card_dataset: Optional[str]
    tenant_datasets: tuple[str, ...]
    reading_kinds: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "dataset": self.dataset,
            "card_dataset": self.card_dataset,
            "tenant_datasets": list(self.tenant_datasets),
            "reading_kinds": list(self.reading_kinds),
        }


def route_verdict(
    source_key: str,
    vendor: str,
    product: str,
    *,
    card_dataset: Optional[str],
    tenant_datasets: Iterable[str],
    reading_kinds: Iterable[str],
) -> RouteVerdict:
    """Decide whether records posted to this collector will land where the detections read.

    Order: native dataset, test channel, ``_raw`` card dataset (matches / xql_only /
    name_differs), disputed tenant name, otherwise cannot_feed.
    """
    dataset = dataset_for(vendor, product)
    tenant = tuple(sorted(set(tenant_datasets)))
    kinds = tuple(sorted(set(reading_kinds)))

    def make(verdict: str) -> RouteVerdict:
        return RouteVerdict(verdict, dataset, card_dataset, tenant, kinds)

    # Names are stored as typed; the native check also compares the canonical form, so
    # "PANW." / "ngfw traffic" cannot slip past as a custom collector.
    if (
        dataset.lower() in NATIVE_DATASETS
        or _canonical(dataset) in NATIVE_DATASETS
        or (card_dataset or "").lower() in NATIVE_DATASETS
    ):
        return make("native_dataset")
    if source_key == TEST_CHANNEL:
        return make("test_channel")
    if card_dataset and card_dataset.endswith("_raw"):
        if dataset.lower() != card_dataset.lower():
            return make("name_differs")
        return make("xql_only" if LIMITED_KINDS & set(kinds) else "matches")
    if card_dataset and tenant and card_dataset not in tenant:
        return make("name_disputed")
    return make("cannot_feed")
