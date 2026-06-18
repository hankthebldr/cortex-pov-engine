#!/usr/bin/env bash
# T1078 — Stitch verification — assert single incident_id spans all 3 planes
# Derived verbatim from SIM-MP-005 step step-04 (../../mp-005-cross-plane-correlation.yml).
# LEGAL: run only in an authorized, isolated lab. See ../README.md.
set -u -o pipefail
echo "[SIM-MP-005/T1078] Stitch verification — assert single incident_id spans all 3 planes"

echo '[MP-005] Stitch verification — polling for stitched incident';
# Allow XSIAM correlation engine to converge. Default ±30s window per
# F2 conventions; real XSIAM correlation usually fires within 60s.
sleep "${CORTEXSIM_STITCH_WAIT_SECONDS:-90}"
echo '[*] Verifier should now run the F2 verification_xql against dataset=xdr_data'
echo '[*] Expected: count_distinct(_product) >= 3, count_distinct(incident_id) = 1'
echo '[*] Failure modes:'
echo '    - 3 separate incidents (stitching window missed) → Cross-Source Correlation Rate fails'
echo '    - Only 2 planes represented (ITDR not ingested) → required_planes_in_incident fails'
echo '    - mttd > 60s → causality timing fails'
