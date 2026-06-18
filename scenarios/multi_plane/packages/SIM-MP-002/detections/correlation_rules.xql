// SIM-MP-002 — XSIAM stitching correlation
// Grouping key: user_principal
// Time window: 60 seconds
// Required planes in incident: ITDR, EDR, NDR
//
// Rules below are the resolved correlation-rule logic from the linked
// TTP card(s) plus the scenario step F2 verification XQL.

// ── Rule CR-CRED-0003 — Credential Access — Chained LSASS + DCSync Within 4h ──
CR-CRED-0001 (LSASS dump) AND CR-CRED-0002 (DCSync) on same actor within 4h — promotes to Critical incident with auto-isolation.

