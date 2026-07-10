// SIM-MP-005 — XSIAM stitching correlation
// Grouping key: src_host
// Time window: 30 seconds
// Required planes in incident: EDR, NDR, ITDR
//
// Rules below are the resolved correlation-rule logic from the linked
// TTP card(s) plus the scenario step F2 verification XQL.

// ── Rule CR-MP-0010 — Cross-Plane Stitch — EDR Plus NDR Plus ITDR From One Source Host ──
(BIOC(MP-005 Interactive Bash From www-data Service Context) AND (BIOC(MP-005 NGFW Outbound HTTP Beacon To Known IOC Domain) OR BIOC(MP-005 Identity Kerberos PreAuth Failure Burst From One Client))) WITHIN 30s GROUP BY src_host HAVING count_distinct(_product) >= 2

// ── F2 stitch verifier (step-04 / CR-MP-0010) ──
// F2 canonical verifier — distinct planes under one incident_id
dataset = xdr_data
| filter _time > timeago(10m)
| comp count_distinct(_product) as plane_count, count_distinct(incident_id) as incident_count by action_local_ip
| filter plane_count >= 3 and incident_count = 1

