---
packet: none (lab canaries on the personal Mac's lab tenant, not a packet reply)
answered: 2026-11-16
answered_by: Claude Code session, personal Mac, lab tenant read back through SimCore's read-only XQL route
source: personal-mac
---

Every source below is our own lab tenant. CortexSim's door probes were sent on 2026-11-10
and 2026-11-13 to one cloud HTTP log collector configured with vendor and product CUSTOM,
log format JSON and gzip compression; its dataset is `custom_custom_raw`. All 19 records
it holds were read back on 2026-11-16 through SimCore's read-only XQL route, together with
the collector's `metrics_source` rows and the SOC Framework's own HTTP-collector dataset on
the same tenant. One collector type and one format: the rules below are provisional until
repeated through an uncompressed collector and through the Broker VM HTTP applet.

```yaml
answers:
  - id: PM-ING-01
    status: partial
    answer: >-
      Every probe landed in the collector's dataset; none was dropped. A body of
      newline-delimited JSON objects was split into one event per line, each with
      its fields parsed (probes JM and N1, `line` 1 and 2). A body that was a JSON
      array (probe A3) and a body that was a bare JSON string were each stored as
      ONE event with no parsed fields and the whole body in `_raw_log`. A record
      whose `timestamp` was `not-a-date` (probe T7) was stored with its other fields
      parsed. The unparsed records show nowhere except `_raw_log`: the collector's
      `metrics_source` rows counted all 19 records as events, with 0 filtered and
      0 XDM events. Invalid JSON, an oversize field and the Broker VM HTTP applet
      were not tested.
    rule: >-
      Landed is not parsed. Send newline-delimited JSON objects, never a JSON array
      or a bare value. A delivery check must find the canary as a parsed field, not
      only count rows, and must treat a canary found only inside `_raw_log` as
      landed-unparsed, not delivered.
    sources:
      - kind: lab_observation
        title: HTTP collector canaries, PM-ING-01 probes A1 to A3, JM and N1
        ref: Lab tenant cloud HTTP log collector (CUSTOM/CUSTOM, JSON, gzip) to custom_custom_raw; 19 records sent 2026-11-10 and 2026-11-13, read back via XQL
        date: 2026-11-16
        distribution: public
      - kind: lab_observation
        title: metrics_source rows for the same collector, 7 days
        ref: Lab tenant metrics_source filtered to the collector; total_event_count 19, filtered_events 0, xdm_events_ingested 0
        date: 2026-11-16
        distribution: public
    follow_ups:
      - >-
        Send one truncated JSON object, one record with a field over 5 MB and one
        uncompressed body to a gzip collector; record which are refused, which land
        unparsed and which vanish.
      - >-
        Repeat the array and bare-value cases through the Broker VM HTTP applet.

  - id: PM-ING-03
    status: partial
    answer: >-
      On the cloud HTTP log collector no body field named `timestamp` or
      `eventTime` set `_time`, whatever its value: an ISO time three days in the
      past, an ISO time one hour in the future, an epoch-milliseconds string, a
      naive `YYYY-MM-DD HH:MM:SS` and `not-a-date` were all filed at arrival, with
      `_time` equal to `_insert_time`, and none was rejected (probes T0 to T8). One
      probe, T4 (the now-3d canary), was filed with `_time` exactly three days
      before the batch's send time and `_insert_time` at arrival, and has no time
      field among its parsed fields. That is consistent with a reserved `_time` key
      in the body being honoured and consumed, but it rests on that one record.
      The SOC Framework's own HTTP-collector dataset on the same tenant agrees:
      about 16,000 records in 7 days, each carrying a `timestamp` field (ISO and
      epoch-seconds forms), all with `_time` equal to `_insert_time`.
    rule: >-
      For a record sent to the cloud HTTP log collector, assume `_time` is its
      arrival time. No other field name or format moves it, and past or future
      stamps are not refused. An emitter that needs a historical `_time`
      (telemetry replay, MTTD baselines) must prove that a body `_time` key works
      before relying on it, and detections on these datasets window on arrival.
    sources:
      - kind: lab_observation
        title: HTTP collector canaries, PM-ING-03 probes T0 to T8
        ref: Lab tenant cloud HTTP log collector (CUSTOM/CUSTOM, JSON, gzip) to custom_custom_raw; _time vs _insert_time per probe, read back via XQL
        date: 2026-11-16
        distribution: public
      - kind: lab_observation
        title: SOC Framework IR execution feed, insert lag by event type, 7 days
        ref: Lab tenant xsiam_socfw_ir_execution_raw (HTTP collector, vendor xsiam); timestamp_diff(_insert_time, _time) was 0 for every event type
        date: 2026-11-16
        distribution: public
    follow_ups:
      - >-
        Send `_time` as epoch milliseconds, epoch seconds and ISO 8601, each three
        days in the past and one hour in the future, and record which forms are
        honoured, clamped or ignored.
      - >-
        Repeat for the Broker VM syslog and HTTP applets and an XDR Collector file
        source, where the answer may differ per data source.
```
