---
packet: none (public-docs research on the personal Mac, not a packet reply)
answered: 2026-11-10
answered_by: Claude Code session, personal Mac, public cortex-docs pages only
source: personal-mac
---

Every source below is a public page fetched on 2026-11-10. Nothing here came from an
internal system. Where a page is silent, the answer says so and stays `partial`.

```yaml
answers:
  - id: PM-ING-02
    status: partial
    answer: >-
      The HTTP log collector names its dataset from the collector instance's own
      Vendor and Product, as `<Vendor>_<Product>_raw`. The page never mentions a
      dataset field in the request body, so a body hint appears to route nothing;
      that last part is our reading of a silence, not a statement on the page.
    rule: >-
      One collector per target dataset, with Vendor/Product spelling the dataset;
      never rely on a `dataset` field in the body.
    sources:
      - kind: public_doc
        title: Set up an HTTP log collector to receive logs
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/http-log-collector/set-up-an-http-log-collector-to-receive-logs.md
        date: 2026-11-10
        distribution: public
        excerpt: creates a dataset with the name <Vendor>_< Product>_raw
    follow_ups:
      - For CEF or LEEF records, does the record's own vendor/product override the collector's?

  - id: PM-ING-05
    status: partial
    answer: >-
      Analytics, IOC and BIOC issues are generated only on normalized logs, and the
      analytics engine runs on all mapped network and authentication data. The
      pages read do not list which XDM fields must be populated, and do not say
      whether correlation rules read the raw or the modeled view.
    rule: >-
      A third-party source that must raise Analytics, BIOC or IOC issues needs to
      be normalized (an XDM modeling rule); raw-only data cannot raise them.
    sources:
      - kind: public_doc
        title: Ingest data from Next-Generation Firewall
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/next-generation-firewall/ingest-data-from-next-generation-firewall
        date: 2026-11-10
        distribution: public
        excerpt: Analytics, IOC, and BIOC issues are only generated on normalized logs.
      - kind: public_doc
        title: Analytics engine
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-engine.md
        date: 2026-11-10
        distribution: public
        excerpt: enables analytics to run on all mapped network and authentication data
    follow_ups:
      - Which XDM fields must a network or authentication record populate to count for analytics?

  - id: PM-ING-07
    status: partial
    answer: >-
      Rate limit 400 requests per second per customer per endpoint, signalled by
      429. Request body up to 10 MB, signalled by 413, with 1 MB recommended. Each
      record at most 5 MB. The collector ingests up to 80,000 events per second.
      No per-tenant daily volume limit is stated on the page.
    rule: >-
      Stay under 400 requests/s per endpoint and about 1 MB per request; map 429 to
      throttled and 413 to too-large in the delivery taxonomy.
    sources:
      - kind: public_doc
        title: Set up an HTTP log collector to receive logs
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/vendor-specific-data-sources-and-connectors/http-log-collector/set-up-an-http-log-collector-to-receive-logs.md
        date: 2026-11-10
        distribution: public
        excerpt: the rate limit is 400 requests per second per customer per endpoint

  - id: PM-STI-09
    status: partial
    answer: >-
      They are separate mechanisms. Stitching joins logs with other Palo Alto
      Networks-generated logs into stories, which are read through xdr_data or a
      stitched preset. Grouping puts issues into a case when they share artifacts
      and attributes, and only when their domain is Security or Posture and their
      severity is Medium or higher. What each one keys on in detail is not stated.
    rule: >-
      `stitching_key` and `required_planes_in_incident` are two contracts: one says
      records form a story, the other says issues share a case.
    sources:
      - kind: public_doc
        title: Ingest data from Next-Generation Firewall
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/palo-alto-networks-integrations/next-generation-firewall/ingest-data-from-next-generation-firewall
        date: 2026-11-10
        distribution: public
        excerpt: the console begins stitching logs with other Palo Alto Networks-generated logs to form stories
      - kind: public_doc
        title: Create a correlation rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md
        date: 2026-11-10
        distribution: public
        excerpt: Issues with shared artifacts and attributes are grouped into a single case only when they meet both criteria

  - id: PM-ANA-02
    status: partial
    answer: >-
      Activation is the shortest log time before a detector can raise an issue;
      Training is the log time it needs to build a baseline. Most but not all
      detectors start running when activation ends. The page does not say whether
      an issue can be raised before training completes, so max(activation,
      training) stays our inference.
    sources:
      - kind: public_doc
        title: Analytics detection time intervals
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-detection-time-intervals.md
        date: 2026-11-10
        distribution: public
        excerpt: Most but not all detectors start running after the activation period ends.

  - id: PM-COR-01
    status: answered
    answer: >-
      Two modes. Real Time rules scan data as it is ingested, accept only some XQL
      stages and must include a filter. Scheduled rules run every 10, 20 or 30
      minutes, hourly, daily or on a custom schedule; the minimum is every 10
      minutes, and the query time frame can be up to 7 days. The docs recommend
      time_frame_end() over current_time().
    rule: >-
      A scheduled correlation sees a run only at its next execution (at most every
      10 minutes) and only inside its time frame (at most 7 days); a
      correlation_window_seconds below 600 cannot mean "evaluated within N seconds"
      for a scheduled rule.
    sources:
      - kind: public_doc
        title: Create a correlation rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md
        date: 2026-11-10
        distribution: public
        excerpt: The minimum query frequency is every 10 minutes and is already configured.
    follow_ups:
      - Does a scheduled rule's time frame window on _time or on insert time (PM-COR-02 stays open)?

  - id: PM-COR-05
    status: partial
    answer: >-
      Suppression is off by default. When enabled, its default duration is 1 hour
      and the maximum is 1 day. The fields suppression keys on were not captured.
    sources:
      - kind: public_doc
        title: Create a correlation rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md
        date: 2026-11-10
        distribution: public
        excerpt: The Duration time can be configured for a maximum of 1 day.
    follow_ups:
      - Which issue fields does correlation suppression key on?

  - id: PM-COR-06
    status: partial
    answer: >-
      At most 1200 user-created scheduled correlations can be active, and any
      correlation rule with 5000 or more hits in 24 hours is disabled
      automatically. No compute cap and no separate Real Time limit is stated.
    rule: >-
      A POV rule pack stays well under 1200 active scheduled rules, and no rule may
      be driven to 5000 hits in 24 hours, emitter volume included.
    sources:
      - kind: public_doc
        title: Create a correlation rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md
        date: 2026-11-10
        distribution: public
        excerpt: automatically disables correlation rules that reach 5000 or more hits over a 24-hour period

  - id: PM-INC-01
    status: partial
    answer: >-
      Issues with shared artifacts and attributes are grouped into one case only
      when their domain is Security or Posture and their severity is Medium or
      higher. Which artifacts count, and whether a time window applies, are not
      stated.
    sources:
      - kind: public_doc
        title: Create a correlation rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md
        date: 2026-11-10
        distribution: public
        excerpt: Issues with shared artifacts and attributes are grouped into a single case only when they meet both criteria
    follow_ups:
      - Which artifacts and attributes count as shared for grouping, and is there a maximum time gap?

  - id: PM-INC-02
    status: partial
    answer: >-
      For correlation-rule issues, Medium and High create a case automatically;
      Low creates no case and is not grouped into an existing one. Henry reports
      that grouping starts at Medium severity in general. Informational is not
      addressed on the page.
    rule: >-
      A detection that must appear under a case must be Medium or higher
      (documented for correlation issues; PM-INC-05 asks whether it holds for every
      source).
    sources:
      - kind: public_doc
        title: Create a correlation rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-correlation-rule/create-a-correlation-rule.md
        date: 2026-11-10
        distribution: public
        excerpt: "Low severity: Do not create cases and are not grouped into existing cases."
      - kind: operator_report
        title: Henry, design conversation
        ref: "things like stitching/grouping started with medium severity"
        date: 2026-11-10
        distribution: public
```
