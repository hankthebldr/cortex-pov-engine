# Provenance — tests/fixtures/tenant_reader/

## issue_search_reply.json

- Recorded: 2026-11-12, from ONE read-only `POST /public_api/v1/issue/search/` against the
  lab tenant alias `deathstar`, sent through SimCore's catalog read
  (`OP-ISSUE-SEARCH`, `dry_run: false`).
- Request body (window values symbolic; they were a lab run's first step time −300 s and
  +3600 s, in epoch ms UTC):

  ```json
  {"request_data": {"filters": [{"field": "_insert_time", "operator": "gte", "value": SINCE_MS},
                                {"field": "_insert_time", "operator": "lte", "value": UNTIL_MS}],
                    "search_from": 0, "search_to": 5,
                    "sort": {"field": "observation_time", "keyword": "asc"},
                    "include_fields": ["normalized_fields"]}}
  ```

- Redaction: every key, the nesting and the envelope (`reply.DATA`, `TOTAL_COUNT`,
  `FILTER_COUNT`) are kept. Every value was replaced with a synthetic one of the same type:
  strings → `synthetic-<n>`; ints → small ints, and epoch-ms values →
  `1762000000000 + n·1000`; host names → `target-01`; IPv4 → `192.0.2.<n>`; ids and
  `case_ids` → small ints that keep the observed sharing (two issues on one case, one on
  another); counts → synthetic ints. Values kept verbatim are public PANW vocabulary only:
  `detection.method` (`TRAPS`, `FW`), `domain` (`Security`), `severity`,
  `status.progress` and `xdm.source.host.os_family` (`LINUX`, `NO_HOST`). The file holds
  no tenant value.
- SimCore's client strips the tenant's outer `reply` key; the fixture restores it, so the
  file is the wire shape the vendor page documents.

## case_search_reply.json, endpoint_get_reply.json

Synthetic, built from the public PANW Cases and Endpoint management API pages
(fetched 2026-11-11). No tenant read.
