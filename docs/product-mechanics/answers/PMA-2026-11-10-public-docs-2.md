---
packet: none (public-docs research on the personal Mac, second pass, not a packet reply)
answered: 2026-11-10
answered_by: Claude Code session, personal Mac, public cortex-docs pages and one PANW blog
source: personal-mac
---

Every source is a public page fetched and checked on 2026-11-10. Nothing came from an
internal system. Questions the pages leave open (the stitch keys, NAT, the time tolerance,
process-start throttling) get no answer here, because an `unknown` record would still read
as provisional.

```yaml
answers:
  - id: PM-STI-05
    status: partial
    answer: >-
      Third-party firewall logs are stitched into network stories, not only PAN-OS
      NGFW logs. The Check Point and Cisco ASA ingestion pages both say so, for
      logs arriving through the Broker VM syslog applet. Check Point logs with
      sessionid 0 are dropped and the destination port exists only in the raw
      logs; Cisco ASA is supported for message IDs 302013 to 302016 only. No page
      says explicitly that a third-party session is linked to an agent process.
    rule: >-
      A third-party firewall scenario may claim a network story only for a
      documented vendor and record type, through the syslog applet; a Check Point
      leg cannot rely on the destination port in the story.
    sources:
      - kind: public_doc
        title: Ingest logs from Check Point firewalls
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/generic-on-premise-data-collectors/broker-vm-data-collector-applets/syslog-collector-applet/check-point-fw1-vpn1/ingest-logs-from-check-point-firewalls
        date: 2026-11-10
        distribution: public
        excerpt: Cortex XSIAM can stitch data from Check Point firewalls with other logs to make up network stories
      - kind: public_doc
        title: Ingest logs from Cisco ASA firewalls and AnyConnect
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/generic-on-premise-data-collectors/broker-vm-data-collector-applets/syslog-collector-applet/cisco-asa-firewalls-and-anyconnect/ingest-logs-from-cisco-asa-firewalls-and-anyconnect
        date: 2026-11-10
        distribution: public
        excerpt: stitching network connection logs with other logs to form network stories
    follow_ups:
      - Is a third-party firewall session in a network story linked to the agent process that made the connection, as a PAN-OS session is?

  - id: PM-STI-07
    status: partial
    answer: >-
      Stitched data lives in the `network_story` and `authentication_story`
      presets over `xdr_data`, and BIOC rules can address it as
      `event_type = STORY`, which covers events combining firewall and endpoint
      logs. Stitched rows carry `story_id`, `story_version`,
      `story_publish_timestamp` and `fw_*` fields. No page names the field that
      says a firewall session was linked to a process.
    rule: >-
      A stitch-proof query reads `network_story` (or `xdr_data` with
      `event_type = STORY`) and requires both the firewall fields and the endpoint
      actor fields to be populated on the same row.
    sources:
      - kind: public_doc
        title: Datasets and presets
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/reference-and-developer-docs/cortex-agentix-xql/get-started-with-xql/datasets-and-presets
        date: 2026-11-10
        distribution: public
        excerpt: These contain information stitched together from Cortex XSIAM agent events and log files to form a common schema.
      - kind: public_doc
        title: Create a BIOC rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-bioc/create-a-bioc-rule
        date: 2026-11-10
        distribution: public
        excerpt: Events relating to a combination of firewall and endpoint logs over the network.
      - kind: public_doc
        title: xdr_data fields
        ref: https://cortex-docs.paloaltonetworks.com/xql-schema-reference/xdr-data-fields
        date: 2026-11-10
        distribution: public
        excerpt: Story publishing timestamp in epoch time.

  - id: PM-BIO-03
    status: partial
    answer: >-
      BIOC field values are evaluated case-insensitively. A BIOC is XQL limited to
      the `filter` and `alter` stages and functions without aggregation. Quoting,
      escaping and whitespace normalization of the command line are not described.
    sources:
      - kind: public_doc
        title: Create a BIOC rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-bioc/create-a-bioc-rule
        date: 2026-11-10
        distribution: public
        excerpt: For BIOC rules, the field values in XQL are evaluated as case insensitive

  - id: PM-BIO-05
    status: answered
    answer: >-
      No default deduplication applies to BIOC issues. Deduplication is applied
      only to issues whose name contains WildFire or Local Analysis, over a 1-hour
      window, and every other issue type is processed individually. A step looped
      20 times raises 20 issues, until the rule reaches 5000 hits in 24 hours and is
      disabled.
    rule: >-
      Expect one BIOC issue per matching event, and keep loops far below 5000
      matches per rule per day.
    sources:
      - kind: public_doc
        title: Issue deduplication
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/investigation-and-response/investigate-issues/issue-deduplication
        date: 2026-11-10
        distribution: public
        excerpt: All other issue types are processed individually and will not be deduped.
      - kind: public_doc
        title: Create a BIOC rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-bioc/create-a-bioc-rule
        date: 2026-11-10
        distribution: public
        excerpt: Disables BIOC rules that reach 5000 or more hits over a 24-hour period.

  - id: PM-BIO-06
    status: partial
    answer: >-
      Yes: creating or enabling a BIOC starts monitoring and also analyzes data
      already collected in the tenant. How far back the historical scan reaches,
      and how soon evaluation starts, are not stated.
    rule: >-
      Importing a BIOC after the run still finds the run, inside an unstated
      history depth; import before the run when MTTD matters, because a match found
      by the backward scan carries no meaningful detection latency.
    sources:
      - kind: public_doc
        title: What's a BIOC?
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-bioc
        date: 2026-11-10
        distribution: public
        excerpt: It also analyzes historical data collected in the tenant.

  - id: PM-BIO-07
    status: partial
    answer: >-
      A BIOC rule may query only `xdr_data` or `cloud_audit_log`, so a predicate
      on `panw_ngfw_traffic_raw` or `cisco_asa_raw` is not a valid BIOC.
      Firewall context is addressable through `event_type = STORY`, which covers
      events combining firewall and endpoint logs. Which STORY fields a predicate
      may use is not listed.
    rule: >-
      A BIOC that needs firewall context targets `xdr_data` with
      `event_type = STORY`; a predicate on a `*_raw` firewall dataset belongs in a
      correlation rule.
    sources:
      - kind: public_doc
        title: Create a BIOC rule
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-a-bioc/create-a-bioc-rule
        date: 2026-11-10
        distribution: public
        excerpt: you can create BIOC rules using the xdr_data and cloud_audit_log datasets

  - id: PM-ABI-01
    status: partial
    answer: >-
      Each ABIOC is tailored to the tenant after the platform analyzes its logs
      and data sources, against user, endpoint and network profiles. No learning
      period for ABIOCs is published.
    sources:
      - kind: public_doc
        title: Analytics issues and Analytics BIOCs
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/analytics/analytics-issues-and-analytics-biocs
        date: 2026-11-10
        distribution: public
        excerpt: Cortex XSIAM tailors each ABIOC to your specific environment after analyzing your logs and data sources
    follow_ups:
      - Does an ABIOC show a pending state while it collects data, and can that state be read through the API?

  - id: PM-IOC-01
    status: partial
    answer: >-
      IOC types are full path, file name, domain, destination IP address, MD5 hash
      and SHA256 hash. Which event fields each type is matched against (DNS query,
      HTTP host, TLS SNI) is not stated.
    sources:
      - kind: public_doc
        title: What's an IOC?
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-an-ioc
        date: 2026-11-10
        distribution: public
        excerpt: You can define the following types of IOCs

  - id: PM-IOC-02
    status: partial
    answer: >-
      Yes: IOCs are matched against all data collected in the past and against new
      data. How soon matching starts after an indicator is added is not stated.
    sources:
      - kind: public_doc
        title: What's an IOC?
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/threat-management/detection-rules/what-are-detection-rules/whats-an-ioc
        date: 2026-11-10
        distribution: public
        excerpt: Cortex XSIAM looks for IOC matches in all data collected in the past

  - id: PM-IOC-05
    status: partial
    answer: >-
      IOC issues are not deduplicated by default, since only WildFire and Local
      Analysis issues are. The default indicator expiration was not checked.
    sources:
      - kind: public_doc
        title: Issue deduplication
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/investigation-and-response/investigate-issues/issue-deduplication
        date: 2026-11-10
        distribution: public
        excerpt: All other issue types are processed individually and will not be deduped.

  - id: PM-INC-01
    status: partial
    answer: >-
      Three grouping methods are documented: artifact association (shared core
      artifacts such as SHA256, host name and user name), exact match detection,
      and related entities within a close timeframe. Issues on the same causality
      chain ID are grouped. Only Security or Posture issues at Medium severity or
      higher qualify. A case stops accepting issues 30 days after creation, 14 days
      after its last issue, or at 1,000 issues. No numeric grouping window is
      published, and the logic is continuously tuned.
    rule: >-
      A multi-plane scenario plants one shared artifact (host, user, hash or causality
      chain) on every plane's issue, keeps each issue at Medium or higher, and
      finishes inside one case's life (well under 14 days between issues).
    sources:
      - kind: public_doc
        title: Case grouping
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/investigation-and-response/case-concepts/case-grouping
        date: 2026-11-10
        distribution: public
        excerpt: Assigned to the Security domain with Medium severity or higher.
      - kind: public_doc
        title: What is Causality?
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/investigation-and-response/case-concepts/what-is-causality
        date: 2026-11-10
        distribution: public
        excerpt: Alerts on the same CID is also one of the methods Cortex XSIAM uses to group alerts into an incident.
      - kind: public_doc
        title: Case thresholds
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/investigation-and-response/overview-of-cases/case-thresholds
        date: 2026-11-10
        distribution: public
        excerpt: 14 days have passed since the last issue was detected.

  - id: PM-INC-05
    status: conflicting
    answer: >-
      The current case-grouping page states the Medium floor in general, not only
      for correlation issues: an issue qualifies when it is in the Security or
      Posture domain with Medium severity or higher. A May 2024 PANW blog says low
      severity alerts may be grouped in identity, cloud and SaaS areas. Henry
      reports that grouping starts at Medium. The docs are newer than the blog,
      but the blog's exception is not contradicted explicitly.
    rule: >-
      Treat Medium as the floor for every source; treat Low identity, cloud or
      SaaS alerts joining a case as possible but unproven.
    sources:
      - kind: public_doc
        title: Case grouping
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/investigation-and-response/case-concepts/case-grouping
        date: 2026-11-10
        distribution: public
        excerpt: Assigned to the Security domain with Medium severity or higher.
      - kind: public_blog
        title: SmartGrouping, precision AI-driven investigation (May 2024)
        ref: https://www.paloaltonetworks.com/blog/security-operations/smartgrouping-precision-ai-driven-investigation/
        date: 2026-11-10
        distribution: public
        excerpt: in some cases like in identity/cloud/SaaS areas, we allow grouping of low severity alerts.
      - kind: operator_report
        title: Henry, design conversation
        ref: "things like stitching/grouping started with medium severity"
        date: 2026-11-10
        distribution: public

  - id: PM-TEN-02
    status: partial
    answer: >-
      The Linux operation mode decides whether anything is collected. Kernel mode
      and User Space mode both collect EDR data; when neither mode's requirements
      are met the agent runs in asynchronous mode, with no event collection, no
      BTP and no EDR. A maximum captured command-line length is not published.
    sources:
      - kind: public_doc
        title: Set up agent settings profiles
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/protect-your-endpoints/endpoint-security/install-and-manage-endpoints/set-up-endpoint-protection/set-up-endpoint-profiles-and-exception-rules/set-up-agent-settings-profiles
        date: 2026-11-10
        distribution: public
        excerpt: there is no event collection, no BTP, no EDR, and no anti-lpe
      - kind: public_doc
        title: Cortex XDR Agent Linux operation modes
        ref: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/insights-and-tips-cortex-xsiam/endpoint-and-agent-management/cortex-xdr-agent-linux-operation-modes
        date: 2026-11-10
        distribution: public
        excerpt: If neither mode's requirements are met, the agent runs in asynchronous mode.

  - id: PM-ALR-01
    status: partial
    answer: >-
      On the Issues API the rule identifier is `detection.rule_id`, a string the
      page does not describe further. The connector reads four other keys and not
      this one. Whether it is stable across content updates is not stated.
    rule: >-
      Read `detection.rule_id` from the Issues API before falling back to technique
      and name.
    sources:
      - kind: public_doc
        title: Issues (Issues APIs)
        ref: https://cortex-docs.paloaltonetworks.com/xsiam-api/issues-apis/issues
        date: 2026-11-10
        distribution: public

  - id: PM-ALR-02
    status: partial
    answer: >-
      On the Issues API, `_insert_time` is the issue creation timestamp in epoch
      milliseconds. `observation_time` exists with no description. The fields
      the connector reads first (`detection_timestamp`, `creation_time`) are not
      on that object.
    sources:
      - kind: public_doc
        title: Issues (Issues APIs)
        ref: https://cortex-docs.paloaltonetworks.com/xsiam-api/issues-apis/issues
        date: 2026-11-10
        distribution: public
        excerpt: Issue creation timestamp in epoch milliseconds.
    follow_ups:
      - What does `observation_time` on an issue record measure?

  - id: PM-ALR-04
    status: partial
    answer: >-
      The source field is `detection.method`, "detection source or method that
      created the issue". Filter values include XDR Agent, XDR Analytics, XDR
      BIOC, XDR IOC, XDR Analytics BIOC, Correlation and PAN NGFW; response codes
      include TRAPS, MAGNIFIER, ANALYTICS_BIOC, BIOC, IOC, CORRELATION and FW.
      Stability across versions is not stated.
    rule: >-
      Map the `detection.method` response codes onto the matcher's signal
      families; an unmapped code is reported, not silently treated as unknown.
    sources:
      - kind: public_doc
        title: Issues (Issues APIs)
        ref: https://cortex-docs.paloaltonetworks.com/xsiam-api/issues-apis/issues
        date: 2026-11-10
        distribution: public
        excerpt: Detection source or method that created the issue.

  - id: PM-ALR-05
    status: partial
    answer: >-
      The Issues API issue object links cases through `case_ids`, a list of the
      case IDs the issue is associated with; it has no `case_id` or `incident_id`
      field. When the change happened, and whether the legacy alerts endpoint the
      connector pages keeps the old names, are not stated.
    sources:
      - kind: public_doc
        title: Issues (Issues APIs)
        ref: https://cortex-docs.paloaltonetworks.com/xsiam-api/issues-apis/issues
        date: 2026-11-10
        distribution: public
        excerpt: List of case IDs this issue is associated with.
```
