# tests/integration/xsiam/test_queries.py
from __future__ import annotations


def test_shape_ingestion_results_maps_contract():
    from integrations.xsiam.queries import shape_ingestion_results
    reply = {"results": {"data": [
        {"source": "okta_audit", "vendor": "okta", "product": "idp",
         "events": 1234, "last_seen": 1717200000000},
        {"dataset": "panw_ngfw", "count": 9, "_last_seen": 1717200001000},
    ]}}
    rows = shape_ingestion_results(reply)
    assert rows[0] == {"source": "okta_audit", "vendor": "okta",
                       "product": "idp", "events": 1234, "last_seen": 1717200000000}
    # Tolerates alternate field names (dataset/count/_last_seen)
    assert rows[1]["source"] == "panw_ngfw"
    assert rows[1]["events"] == 9
    assert rows[1]["last_seen"] == 1717200001000


def test_shape_ingestion_results_handles_empty():
    from integrations.xsiam.queries import shape_ingestion_results
    assert shape_ingestion_results({}) == []
    assert shape_ingestion_results({"results": {}}) == []


def test_ingestion_query_constant_exists():
    from integrations.xsiam.queries import INGESTION_HEALTH_XQL
    assert isinstance(INGESTION_HEALTH_XQL, str) and INGESTION_HEALTH_XQL.strip()
