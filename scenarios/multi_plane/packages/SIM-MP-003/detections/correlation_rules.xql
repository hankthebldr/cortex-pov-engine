// SIM-MP-003 — XSIAM stitching correlation
// Grouping key: src_host
// Time window: 60 seconds
// Required planes in incident: EDR, NDR
//
// Rules below are the resolved correlation-rule logic from the linked
// TTP card(s) plus the scenario step F2 verification XQL.

// ── Rule CR-MP-0004 — Staged DNS Exfil Cross-Plane Stitch — XDR Staging + NGFW DNS Anomaly ──
(BIOC(MP-003 Tar Gzip Compression Of Tmp Staging By Service Account) AND BIOC(MP-003 Dig TXT Tight Loop From Service Account) AND XQL(MP-003 NGFW DNS TXT Tunneling Burst High Entropy Labels)) -> join on hostname within 60s

