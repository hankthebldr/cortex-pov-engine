// SIM-MP-001 — XSIAM stitching correlation
// Grouping key: src_host
// Time window: 60 seconds
// Required planes in incident: EDR, NDR
//
// Rules below are the resolved correlation-rule logic from the linked
// TTP card(s) plus the scenario step F2 verification XQL.

// ── Rule CR-MP-0001 — C2 Beacon Cross-Plane Stitch — NGFW Session + XDR Process ──
(XQL(MP-001 NGFW Repetitive HTTP Beacon To testmynids) AND (BIOC(MP-001 Curl Beacon With Suspicious User-Agent From www-data) OR BIOC(MP-001 Interactive Shell Spawned From www-data Service Context))) -> join on src_host within 60s

// ── Rule CR-MP-0002 — DNS Tunnel Exfil Cross-Plane Stitch — NGFW DNS Anomaly + XDR Dig Loop ──
(XQL(MP-001 NGFW DNS TXT High Entropy Burst) AND XQL(MP-001 Repetitive Dig TXT From Service Account)) -> join on src_host within 60s

// ── F2 stitch verifier (step-02 / CR-MP-0001) ──
// F2 stitching verifier — distinct planes under one incident_id
dataset = xdr_data
| filter actor_effective_user_name = "www-data"
| comp count_distinct(_product) as plane_count, count_distinct(incident_id) as incident_count by action_local_ip
| filter plane_count >= 2 and incident_count = 1

