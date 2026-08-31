#!/usr/bin/env python3
"""Author the v2.2 UC/TC crosswalk and apply it to the scenario corpus.

The master index binds one POV-SC *payload* to many test cases, so an engine
scenario evidences a SET of test cases. This file is the hand-authored mapping
from each scenario to that set — string matching was tried and rejected
(8 confident matches out of 161: the index speaks in capability language,
the corpus in adversary language, and the two vocabularies barely overlap).

Ordering matters: ``CROSSWALK[sid][0]`` is the primary ``tc_ref``; the rest
become ``tc_refs``. ``pov_scenario_id`` is derived, not authored — a payload is
*defined* by its bound TC set, so the best-overlapping payload is the one this
scenario instantiates.

Usage::

    python3 scripts/uctc_crosswalk_v2.2.py --report   # print the reconciliation
    python3 scripts/uctc_crosswalk_v2.2.py --emit     # write the CSV artifacts
    python3 scripts/uctc_crosswalk_v2.2.py --apply    # rewrite scenario YAMLs
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(REPO, "docs", "uc_tc_mapping", "_v2.2-source")
OUT = os.path.join(REPO, "docs", "uc_tc_mapping")

# Resolution vocabulary
REMAP = "REMAP"        # binds honestly to existing v2.2 test cases
NETNEW = "NET-NEW"     # the index carries no TC for what this proves; the
                       # primary ref is the nearest honest capability match and
                       # a proposed v2.3 TC is emitted alongside it

# ---------------------------------------------------------------------------
# The crosswalk: scenario_id -> (tc_refs, resolution, rationale)
# tc_refs[0] is the primary binding.
# ---------------------------------------------------------------------------

CROSSWALK: dict[str, tuple[list[str], str, str]] = {

    # ── EDR ────────────────────────────────────────────────────────────────
    # Every endpoint scenario carries the causality contract, so TC-EDR-05
    # (full process chain + causality-owner attribution) is the plane's spine,
    # with TC-EDR-03 (multi-method behavioral detection) where the scenario
    # defeats the static layer.
    #
    # TC-CITH-03/04 are deliberately ABSENT here. Their titles read
    # plane-agnostic ("Behavioral IOC Detection", "AI Runtime Security
    # Analytics") but the index rows are cloud-workload-scoped: products =
    # Cortex Cloud, dataset = cloud_audit_logs · container_events. Because
    # required_addons is unioned over every tc_ref, binding them here made 23
    # non-cloud scenarios demand Cortex Cloud + Cloud Runtime — a POV-scoping
    # error that would have quoted the wrong SKUs. Keep them on CDR/AI_SPM/
    # CLOUD_APP rows only.
    "SIM-EDR-001": (["TC-EDR-03", "TC-EDR-05"], REMAP,
        "Credential dumping via a behavioral chain — multi-method detection plus CGO attribution."),
    "SIM-EDR-002": (["TC-EDR-03", "TC-EDR-05"], REMAP,
        "Reverse-shell callback detected behaviorally with the spawning chain intact."),
    "SIM-EDR-003": (["TC-EDR-05"], REMAP,
        "Cron/systemd persistence is proven by the process lineage, not by a malware verdict."),
    "SIM-EDR-004": (["TC-EDR-05"], REMAP,
        "Log tampering and timestomping are only visible as causality-anchored BIOC events."),
    "SIM-EDR-005": (["TC-IR-04", "TC-EDR-05"], REMAP,
        "SSH abuse and internal recon is the canonical simulated lateral-movement pattern."),
    "SIM-EDR-006": (["TC-EDR-03", "TC-EDR-05", "TC-ITDR-01"], REMAP,
        "comsvcs.dll LSASS dump is living-off-the-land credential abuse with a LOLBin chain."),
    "SIM-EDR-007": (["TC-EDR-04", "TC-EDR-03", "TC-EDR-05"], REMAP,
        "ESXi inhibit-recovery is the auto-containment trigger case on confirmed destructive execution."),
    "SIM-EDR-008": (["TC-EDR-04", "TC-EDR-03", "TC-EDR-05"], REMAP,
        "Host mass-encryption — containment must fire before the impact stage completes."),
    "SIM-EDR-009": (["TC-DLP-01", "TC-EDR-05"], REMAP,
        "Rclone bulk egress is sensitive-data exfiltration observed on the endpoint channel."),
    "SIM-EDR-010": (["TC-EDR-03", "TC-EDR-05"], REMAP,
        "Hands-on-keyboard ABIOC — anomalous behavior on a learned automation-account baseline."),
    "SIM-EDR-011": (["TC-EDR-04", "TC-EDR-05"], REMAP,
        "Pre-encryption file-I/O anomaly is the earliest containment signal for ransomware."),
    "SIM-EDR-012": (["TC-DLP-05", "TC-EDR-05"], REMAP,
        "Collection/staging anomaly on a service process — behavioral analytics over data access."),
    "SIM-EDR-013": (["TC-EDR-03", "TC-EDR-05"], REMAP,
        "Fileless UUID-shellcode staging defeats static analysis; the behavioral method must carry it."),
    "SIM-EDR-014": (["TC-WAAS-04", "TC-EDR-03", "TC-EDR-05"], REMAP,
        "ASPX web shell loading a .NET backdoor into w3wp is agent-based runtime web-app detection."),
    "SIM-EDR-015": (["TC-IR-04", "TC-EDR-05"], REMAP,
        "Chisel tunnel plus PsExec fan-out is lateral movement with a tool-transfer precursor."),
    "SIM-EDR-016": (["TC-EDR-03", "TC-EDR-05"], REMAP,
        "ClickFix pastejacking — user-executed interpreter chain parented by explorer.exe."),
    "SIM-EDR-017": (["TC-EDR-03", "TC-EDR-05"], REMAP,
        "mshta to extrac32 CAB unpack — interpreter depth is the behavioral discriminator."),
    "SIM-EDR-018": (["TC-IR-12", "TC-IR-05", "TC-CITH-07"], REMAP,
        "Measures AI SOC narrative synthesis over a 72-minute identity-to-exfil incident."),
    "SIM-EDR-019": (["TC-EDR-04", "TC-EDR-03", "TC-EDR-05", "TC-IR-05"], REMAP,
        "Akira vCenter-to-ESXi — cross-surface chain terminating in containment-worthy impact."),
    "SIM-EDR-020": (["TC-WAAS-04", "TC-EDR-03", "TC-ITDR-01", "TC-EDR-05"], REMAP,
        "Web shell to in-memory Mimikatz — runtime web-app compromise into credential access."),
    "SIM-EDR-021": (["TC-EDR-03", "TC-EDR-05"], REMAP,
        "BYOVD EDR-kill — the driver-load anomaly must be caught before the sensor goes blind."),
    "SIM-EDR-022": (["TC-EDR-05", "TC-EDR-03"], REMAP,
        "Shelf-staged privesc enumeration — the causality claim is the point: walk back from the "
        "enumeration burst to the apache2 CGO. Adds NO new index coverage (both rows are already "
        "evidenced); it adds a SECOND, behavioural-not-name-keyed piece of evidence and the delivery "
        "path that works inside a default-deny network."),

    # ── CDR ────────────────────────────────────────────────────────────────
    "SIM-CDR-001": (["TC-CDR-01", "TC-CITH-03"], REMAP,
        "Container enumeration is the entry runtime-detection case for workloads."),
    "SIM-CDR-002": (["TC-CDR-01", "TC-CITH-02"], REMAP,
        "XMRig in a container — known-malware detection on a cloud workload."),
    "SIM-CDR-003": (["TC-CDR-01", "TC-CITH-03"], REMAP,
        "Privileged-mode container escape detected at runtime with the escaping process chain."),
    "SIM-CDR-004": (["TC-CDR-01", "TC-IR-04"], REMAP,
        "Kubernetes lateral movement and persistence across pods."),
    "SIM-CDR-005": (["TC-CITH-02", "TC-CDR-01"], REMAP,
        "WildFire verdict on a simulated backdoor retrieved into a workload."),
    "SIM-CDR-006": (["TC-CDR-01", "TC-EDR-05"], REMAP,
        "Node-level systemd/cron persistence beneath the container boundary."),
    "SIM-CDR-007": (["TC-CDR-03", "TC-CVM-01", "TC-SSCAN-01", "TC-IACS-01"], REMAP,
        "Posture sweep wiring Trivy/kube-bench/Gitleaks/Cloudsplaining — vulns, secrets, and IaC misconfig in one pass."),
    "SIM-CDR-008": (["TC-CIEM-03", "TC-DSPM-04", "TC-CDR-02"], REMAP,
        "IAM key abuse into S3 exfil — entitlement escalation plus anomalous data access."),
    "SIM-CDR-009": (["TC-CITH-04", "TC-CDR-01"], REMAP,
        "Sock-shop microservices ABIOC — anomalous east-west behavior on a learned service baseline."),
    "SIM-CDR-010": (["TC-CDR-01", "TC-CITH-02", "TC-EDR-04"], REMAP,
        "Container-native ransomware escaping to the host filesystem."),
    "SIM-CDR-011": (["TC-CDR-01", "TC-CITH-03"], REMAP,
        "Kubernetes-Goat escape chain — multi-stage runtime detection with lineage."),
    "SIM-CDR-012": (["TC-CITH-02", "TC-CDR-03"], REMAP,
        "WildFire sample retrieval in-container against a misconfigured pod."),
    "SIM-CDR-013": (["TC-CITH-02", "TC-CDR-01"], REMAP,
        "Busybox pod fetching and detonating ELF payloads from an insecure deployment."),
    "SIM-CDR-014": (["TC-CITH-04", "TC-CDR-01", "TC-CDR-02"], REMAP,
        "Malicious in-cluster multi-stage chain — the ABIOC terminal for the CDR plane."),
    "SIM-CDR-015": (["TC-BYOML-02", "TC-TH-03"], REMAP,
        "XDM modeling-rule substrate proof — datamodel normalization is exactly the BYOML datamodel-customization case."),
    "SIM-CDR-016": (["TC-CIEM-03", "TC-CITH-04"], REMAP,
        "Cloud workload IAM usage anomaly against a learned entitlement baseline."),
    "SIM-CDR-017": (["TC-CIEM-03", "TC-CDR-02", "TC-SSCAN-01"], REMAP,
        "Leaked .env credential into self-propagating Lambda extortion. The runtime "
        "entitlement abuse is the claim; the planted secret is the precondition, so "
        "TC-SSCAN-01 rides as secondary rather than driving the binding."
),
    "SIM-CDR-018": (["TC-CIEM-03", "TC-CDR-01"], REMAP,
        "IAM Roles Anywhere trust-anchor abuse for certificate-backed persistence."),
    "SIM-CDR-019": (["TC-DSPM-04", "TC-CITH-04"], REMAP,
        "S3 mass-object exfiltration by a suspicious identity — the canonical DDR case."),
    "SIM-CDR-020": (["TC-CITH-04", "TC-CIEM-03"], REMAP,
        "CloudTrail secret-harvesting chain across Secrets Manager and SSM. Runtime "
        "API behavior, not file scanning — TC-SSCAN-01 scores planted secrets in "
        "code/config/IaC and this scenario plants none."
),
    "SIM-CDR-021": (["TC-CIEM-03", "TC-CITH-04"], REMAP,
        "IAM and S3 enumeration burst — reconnaissance over the cloud control plane."),
    "SIM-CDR-022": (["TC-DSPM-04", "TC-CITH-04"], REMAP,
        "BigQuery foreign-project exfil via an impersonated service account."),
    "SIM-CDR-023": (["TC-CIEM-03", "TC-CITH-04"], REMAP,
        "IMDS token theft followed by remote use — credential-to-entitlement pivot."),
    "SIM-CDR-024": (["TC-CITH-04", "TC-CDR-01"], REMAP,
        "Compute in a dormant region is a pure baseline-deviation signal."),
    "SIM-CDR-025": (["TC-CDR-01", "TC-CITH-04"], REMAP,
        "Unusual pod exec plus unusual service-account API call from Kubernetes audit."),
    "SIM-CDR-026": (["TC-CDR-01", "TC-CITH-04"], REMAP,
        "First-time Kubernetes secret enumeration and access, observed in the audit "
        "log. Runtime access to secrets, not static scanning of files."
),
    "SIM-CDR-027": (["TC-DSPM-04"], REMAP,
        "RDS PostgreSQL mass dump via a compromised web identity — the scenario's own "
        "uc_ref/tc_ref already bind TC-DSPM-04, the DDR anomalous-data-access row; "
        "TC-DSPM-04 is already evidenced (SIM-CDR-008/019/022, SIM-CLOUD-006/008/009/010), "
        "so this is a second, database-runtime-specific proof, not new index coverage."),
    "SIM-CDR-028": (["TC-DSPM-04"], REMAP,
        "DynamoDB NoSQL table scan and exfiltration via an over-permissioned IAM role — "
        "same TC-DSPM-04 binding as SIM-CDR-027, a second data-store shape (NoSQL vs. "
        "relational) proving the same DDR claim, not new index coverage."),

    # ── NDR ────────────────────────────────────────────────────────────────
    # TC-NDR-03 is the EAL case specifically — these scenarios exist to prove
    # the analytics engine reads Enhanced Application Logs.
    "SIM-NDR-001": (["TC-NDR-03", "TC-NDR-01", "TC-NDR-04"], REMAP,
        "C2 beaconing via EAL, stitched to the originating endpoint process."),
    "SIM-NDR-002": (["TC-NDR-03", "TC-NDR-04"], REMAP,
        "DNS tunnelling — EAL-derived analytics over query volume and entropy."),
    "SIM-NDR-003": (["TC-NDR-03", "TC-NDR-04"], REMAP,
        "Stratum App-ID identifies cryptojacking egress the payload alone would not reveal."),
    "SIM-NDR-004": (["TC-NDR-03", "TC-IR-04"], REMAP,
        "SMB/RPC sweep is lateral movement observed from the network plane."),
    "SIM-NDR-005": (["TC-NDR-04", "TC-DLP-01"], REMAP,
        "Bulk HTTPS egress crossing the exfiltration threshold."),
    "SIM-NDR-006": (["TC-NDR-03", "TC-NDR-04", "TC-DLP-01"], REMAP,
        "FTP cleartext egress carrying credentials — App-ID plus data-channel exposure."),
    "SIM-NDR-007": (["TC-NDR-03", "TC-NDR-04"], REMAP,
        "SSH outbound with an atypical client banner — App-ID against a tunnel."),
    "SIM-NDR-008": (["TC-NDR-04"], REMAP,
        "Low-and-slow jittered beacon only resolves against a learned host-egress baseline."),
    "SIM-NDR-009": (["TC-NDR-04"], REMAP,
        "DNS-tunnel anomaly against a learned per-host DNS baseline."),
    "SIM-NDR-010": (["TC-NDR-02", "TC-NDR-04"], REMAP,
        "Edge-appliance telemetry collapse plus WebVPN C2 — the network story needs endpoint context to read."),
    "SIM-NDR-011": (["TC-NDR-04"], REMAP,
        "Telegram Bot API as allowlisted-SaaS C2 — behavioral, since the destination is reputable."),
    "SIM-NDR-012": (["TC-NDR-03", "TC-NDR-04", "TC-DLP-01"], REMAP,
        "NGFW EAL rare-domain beacon escalating to a massive HTTPS upload."),

    # ── ITDR ───────────────────────────────────────────────────────────────
    "SIM-ITDR-001": (["TC-ITDR-02"], REMAP,
        "Impossible travel across Okta sign-ins — a direct 1:1 with the index test case."),
    "SIM-ITDR-002": (["TC-ITDR-01", "TC-ITDR-05"], REMAP,
        "MFA fatigue / push bombing feeds the rolling user risk score."),
    "SIM-ITDR-003": (["TC-ITDR-01", "TC-ITDR-05"], REMAP,
        "Credential stuffing burst across many principals."),
    "SIM-ITDR-004": (["TC-ITDR-02", "TC-ITDR-01"], REMAP,
        "Session-token replay across geo and user-agent — the same geo-analytic as impossible travel."),
    "SIM-ITDR-005": (["TC-ITDR-01"], REMAP,
        "Brute force to account lockout on the Microsoft IdP."),
    "SIM-ITDR-006": (["TC-ITDR-01", "TC-ITDR-03"], REMAP,
        "AS-REP Roast and Kerberoast harvest — on-prem AD credential abuse correlated through CIE."),
    "SIM-ITDR-007": (["TC-ITDR-01", "TC-ITDR-03", "TC-EDR-05"], REMAP,
        "AD privilege-escalation chain from LSASS through DACL abuse to Kerberos."),
    "SIM-ITDR-008": (["TC-ITDR-01", "TC-ITDR-05"], REMAP,
        "Help-desk MFA reset via voice impersonation — social engineering surfaced as identity risk."),
    "SIM-ITDR-009": (["TC-ITDR-02", "TC-ITDR-05"], REMAP,
        "Sign-in account takeover against a learned identity baseline."),
    "SIM-ITDR-010": (["TC-ITDR-03", "TC-ITDR-05"], REMAP,
        "Anomalous privileged-role and consent grant on a directory-sync account."),
    "SIM-ITDR-011": (["TC-ITDR-05"], REMAP,
        "Compromised Intune admin issuing a mass device wipe — identity-weaponized impact."),
    "SIM-ITDR-012": (["TC-ITDR-01"], REMAP,
        "GlobalProtect authentication bypass — a session established without authentication."),
    "SIM-ITDR-013": (["TC-ITDR-01", "TC-ITDR-03"], REMAP,
        "BadSuccessor dMSA privilege escalation on Server 2025."),
    "SIM-ITDR-014": (["TC-ITDR-03", "TC-ITDR-01"], REMAP,
        "ROADtools/roadtx Entra token abuse — rogue device registration to PRT theft."),
    "SIM-ITDR-015": (["TC-ITDR-01", "TC-ITDR-03", "TC-IR-04"], REMAP,
        "Internal AD kill chain: enumeration, LLMNR poisoning, offline cracking, lateral movement."),
    "SIM-ITDR-016": (["TC-IR-07", "TC-ITDR-02", "TC-SOAR-02"], REMAP,
        "Closed-loop auto-disable — the eradication-and-recovery playbook executing end to end."),
    "SIM-ITDR-017": (["TC-ITDR-01", "TC-ITDR-03"], REMAP,
        "AD CS ESC1 template abuse — SAN-mismatch enrollment into PKINIT."),
    "SIM-ITDR-018": (["TC-ITDR-03", "TC-ITDR-05"], REMAP,
        "Azure service-principal token abuse from an unknown tenant."),
    "SIM-ITDR-019": (["TC-ITDR-03", "TC-AES-07"], REMAP,
        "Conditional-access weakening followed by covert mailbox-rule persistence — identity control drift into mail exfil."),
    "SIM-ITDR-020": (["TC-ITDR-01", "TC-IR-04"], REMAP,
        "Kerberoast weak-ticket burst followed by pass-the-hash lateral movement."),

    # ── ANALYTICS (multi-plane) ────────────────────────────────────────────
    # TC-IR-05 is the plane's reason to exist: correlation across endpoint,
    # network, and identity telemetry.
    "SIM-MP-001": (["TC-NDR-01", "TC-IR-05"], REMAP,
        "C2 beacon stitching NGFW sessions to XDR process lineage."),
    "SIM-MP-002": (["TC-IR-05", "TC-IR-04", "TC-ITDR-01"], REMAP,
        "Kerberoast to pass-the-hash to DCSync across ITDR, EDR, and NDR."),
    "SIM-MP-003": (["TC-IR-05", "TC-DLP-02", "TC-NDR-04"], REMAP,
        "Staged exfil via DNS tunnel — cross-channel correlation of endpoint staging with network egress."),
    "SIM-MP-004": (["TC-IR-05", "TC-CIEM-03"], REMAP,
        "APT29-style cloud credential theft into lateral movement and exfil."),
    "SIM-MP-005": (["TC-IR-05", "TC-IR-13"], REMAP,
        "The cross-plane MOAT scenario — it already names TC-IR-05 in its title."),
    "SIM-MP-006": (["TC-NDR-01", "TC-CDR-02"], REMAP,
        "NGFW container egress stitched to endpoint process causality on a 5-tuple plus 10s window."),
    "SIM-MP-007": (["TC-IR-05", "TC-WAAS-03"], REMAP,
        "Staged exposure to runtime exploit to impact — the dual-control causality proof."),
    "SIM-MP-008": (["TC-IR-05", "TC-CITH-07"], REMAP,
        "Identity to endpoint to network hands-on-keyboard campaign."),
    "SIM-MP-009": (["TC-IR-05"], REMAP,
        "Machine-tempo cloud chain driven by an autonomous agent swarm."),
    "SIM-MP-010": (["TC-IR-05", "TC-WAAS-03"], REMAP,
        "Ivanti pre-auth overflow pivoting to an internal Windows host."),
    "SIM-MP-011": (["TC-IR-05", "TC-WAAS-04"], REMAP,
        "Phantom Taurus NET-STAR — IIS backdoor with Ntospy credential interception."),
    "SIM-MP-012": (["TC-IR-05", "TC-CITH-07"], REMAP,
        "BYOVD sensor tamper stitched to C2 and rclone exfil — the blinding race."),
    "SIM-MP-013": (["TC-IR-02", "TC-IR-13", "TC-CITH-07"], REMAP,
        "Seven signals collapsing to one incident — the alert-stitching noise-reduction case exactly."),
    "SIM-MP-014": (["TC-IR-05", "TC-ITDR-03"], REMAP,
        "AzureHound cloud-identity enumeration after token theft."),
    "SIM-MP-015": (["TC-IR-01", "TC-CITH-11", "TC-IR-03", "TC-TH-05", "TC-SOT-02"], REMAP,
        "Triage discrimination under decoy noise — true-positive elevation measured "
        "against false-positive rate, which is exactly the smart-scoring "
        "FP-reduction case (TC-TH-05). TC-SOT-02 is added on the index's own "
        "evidence, not on title similarity: it carries TC-IR-01's success_criteria "
        "string verbatim, the same primary_kpi (Triage Automation Rate), the same "
        ">60% threshold, the same detection_source (dataset=incidents) and the same "
        "POV-SC-006 payload, and TC-IR-01 is already this scenario's primary ref. "
        "This scenario asserts clauses 1-2 of that shared criteria (auto-triage "
        "classifies the corpus; ranking puts the malicious incident above the "
        "benign decoys). RESIDUE, disclosed rather than papered over: clause 3, "
        "'analyst override feedback loop functions correctly', is proven by "
        "NEITHER binding — it needs a human clicking override and the model "
        "consuming that as training signal. TC-IR-01 inherits the same caveat."
),
    "SIM-MP-016": (["TC-ITDR-03", "TC-IR-05"], REMAP,
        "Okta admin takeover into a rogue federated IdP and cross-cloud resource access."),
    "SIM-MP-017": (["TC-IR-05", "TC-CDR-02"], REMAP,
        "React2Shell pod RCE pivoting to the cloud control plane."),
    "SIM-MP-018": (["TC-IR-05", "TC-SSCAN-01"], REMAP,
        "TeamPCP supply chain — CI runner /proc memory scrape into cloud "
        "credentials. The proof is the cross-plane correlation; there is no "
        "dependency CVE here, so TC-SCA-01 does not apply."
),
    "SIM-MP-019": (["TC-IR-05", "TC-WAAS-03", "TC-IR-04"], REMAP,
        "Kali external-to-internal kill chain across recon, exploit, credential access, and lateral movement."),
    "SIM-MP-020": (["TC-EDR-04", "TC-IR-07", "TC-SOAR-02"], REMAP,
        "BlackSuit ransomware precursor triggering auto-containment before the ESXi impact stage."),
    "SIM-MP-021": (["TC-AGTX-03", "TC-IR-01", "TC-IR-02", "TC-AES-08"], REMAP,
        "An 800-alert LABELLED corpus, so precision, recall and the grouping "
        "collapse ratio are computed against an author-time truth column rather "
        "than asserted. TC-AGTX-03 is NET-NEW evidence and is the honest "
        "primary: the row scores AI-driven triage classification at 90% against "
        "a diverse submitted alert set, which is exactly the measurement. "
        "TC-AES-08 is bound with a stated limit — the row says 'across all "
        "email platforms' and this measures disposition on the TWO the engine "
        "ingests (Proofpoint TAP and M365), which the scenario says out loud. "
        "TC-AGTX-06 is deliberately NOT bound: it demands forensics and "
        "containment across CrowdStrike and Defender, and CortexSim cannot emit "
        "either, so binding it would claim credit for a leg the engine "
        "physically cannot run."),
    "SIM-MP-022": (["TC-DLP-02", "TC-IR-05", "TC-DLP-01"], REMAP,
        "Encryption-less extortion — staging, chunked sub-threshold egress and "
        "a ransom note with NO T1486 anywhere in the run. Every bound row is "
        "already evidenced, so this moves the coverage number by zero and says "
        "so. Its value is that every other impact scenario in the library "
        "resolves on an encryption crescendo, and Unit 42 puts encryption in a "
        "shrinking minority of extortion cases. Carries a benign comparison arm "
        "with a matching I/O shape, because an alert on the malicious arm alone "
        "only proves the platform alerts on bulk file activity."),
    "SIM-APB-001": (["TC-APB-06", "TC-SOAR-02"], REMAP,
        "Governed-autonomy audit reconstruction across three structurally "
        "different action classes. TC-APB-06 is NET-NEW and is the ONLY "
        "unevidenced P1 row in the index carrying a measurable threshold "
        "(100% of actions reconstructable) — the other eight P1 rows are scored "
        "'Qualitative pass', which verifier.score_run clamps to not_applicable, "
        "so authoring them raises a count that can never go green. Three arms "
        "rather than one because an audit trail can be complete for the action "
        "class a vendor demoed and threadbare for every other."),

    # ── EMAIL ──────────────────────────────────────────────────────────────
    "SIM-EMAIL-001": (["TC-AES-01", "TC-AES-06"], REMAP,
        "Phishing credential-harvest link — AI phishing detection plus link-payload analysis."),
    "SIM-EMAIL-002": (["TC-AES-05"], REMAP,
        "Malicious attachment: macro, ISO, and HTML-smuggling variants."),
    "SIM-EMAIL-003": (["TC-AES-03"], REMAP,
        "BEC and executive impersonation — a direct 1:1 with the index test case."),
    "SIM-EMAIL-004": (["TC-AES-01", "TC-AES-06"], REMAP,
        "Thread-hijack reply-chain phishing."),
    "SIM-EMAIL-005": (["TC-AES-02", "TC-ITDR-02"], REMAP,
        "AiTM session-token theft — the cross-signal link from phishing to downstream credential compromise."),

    # ── AIRS ───────────────────────────────────────────────────────────────
    "SIM-AIRS-001": (["TC-AIRS-07"], REMAP,
        "Direct prompt injection (LLM01) — real-time detection and blocking."),
    "SIM-AIRS-002": (["TC-AIRS-07", "TC-AIRS-01"], REMAP,
        "Indirect injection via a RAG document — the poisoned-content architectural case."),
    "SIM-AIRS-003": (["TC-AIRS-07"], REMAP,
        "System-prompt leakage (LLM07)."),
    "SIM-AIRS-004": (["TC-AIRS-08", "TC-AGTX-07"], REMAP,
        "Excessive agency / tool-call abuse — anomalous model behavior against agent guardrails."),
    "SIM-AIRS-005": (["TC-AIRS-08"], REMAP,
        "Unbounded consumption — token-exhaustion DoS as a runtime behavioral anomaly."),

    # ── AI_SPM ─────────────────────────────────────────────────────────────
    # A clean 1:1 with UC-AISP. Note these are POS-class assertions: the
    # scenarios validate posture findings, not fired detections, so S-14 fires
    # by design and Phase 4 should treat them as fixtures.
    "SIM-AISPM-001": (["TC-AISP-01"], REMAP,
        "AI asset discovery and inventory — shadow-AI detection. 1:1 with the index."),
    "SIM-AISPM-002": (["TC-AISP-02"], REMAP,
        "AI model security assessment — overprivileged role and misconfiguration findings."),
    "SIM-AISPM-003": (["TC-AISP-03"], REMAP,
        "AI ecosystem and supply-chain risk over vulnerable ML dependencies."),
    "SIM-AISPM-004": (["TC-AISP-04"], REMAP,
        "AI static risk analysis — hardcoded credentials, insecure pickle, unvalidated input."),
    "SIM-AISPM-005": (["TC-AISP-05"], REMAP,
        "AI sensitive-data classification across training sets."),
    "SIM-AISPM-006": (["TC-AISP-06"], REMAP,
        "AI security dashboard and aggregate posture view."),
    "SIM-AISPM-007": (["TC-CITH-04", "TC-CIEM-03"], REMAP,
        "Vertex AI double agent stealing a P4SA token — runtime, not posture, so it binds DET rows."),

    # ── ASM ────────────────────────────────────────────────────────────────
    "SIM-ASM-001": (["TC-ASM-05", "TC-ASM-06"], REMAP,
        "Internet-exposed surface discovery covering registered ranges and service identification."),
    "SIM-ASM-002": (["TC-ASM-06", "TC-CVM-03"], REMAP,
        "Inbound vulnerability-scan recon correlating exposure with vulnerable services."),
    "SIM-ASM-003": (["TC-ASM-01", "TC-ASM-05"], REMAP,
        "Passive OSINT victim-info gathering feeding unmanaged-asset context."),
    "SIM-ASM-004": (["TC-WAAS-03", "TC-ASM-06"], REMAP,
        "Web-application enumeration and injection — the OWASP Top 10 detection case."),
    "SIM-ASM-005": (["TC-CVM-03", "TC-WAAS-03", "TC-ASM-07"], REMAP,
        "Internet-exposed WSUS (CVE-2025-59287) escalating from exposure to RCE."),
    "SIM-ASM-006": (["TC-ASM-03", "TC-ASM-01"], REMAP,
        "Scattered Spider OSINT and help-desk profiling — Unit 42 TI against ASM findings."),

    # ── CSPM ───────────────────────────────────────────────────────────────
    "SIM-CSPM-001": (["TC-CSPM-02", "TC-CSPM-01", "TC-CSPM-04"], REMAP,
        "Posture misconfiguration sweep against planted findings — OOTB policy evaluation."),
    "SIM-CSPM-002": (["TC-CSPM-04", "TC-CSPM-03"], REMAP,
        "Posture drift correlation surfacing as prioritized cases."),
    "SIM-CSPM-003": (["TC-CIEM-03", "TC-CIEM-01", "TC-CSPM-02"], REMAP,
        "IAM trust-policy and PassRole finding correlated to a runtime AssumeRole."),
    "SIM-CSPM-004": (["TC-CSPM-02", "TC-DSPM-03"], REMAP,
        "Unencrypted-snapshot and KMS-gap findings correlated to cross-account sharing."),
    "SIM-CSPM-005": (["TC-SSCAN-01", "TC-ASPM-01", "TC-ERV-05"], REMAP,
        "Exposed .env to stolen-key S3 mass export — the code-to-cloud traceability chain end to end."),

    # ── TIM ────────────────────────────────────────────────────────────────
    "SIM-TIM-001": (["TC-TH-02", "TC-TIM-02", "TC-XTI-03", "TC-NDR-06"], REMAP,
        "TAXII feed match stitched to an outbound session, then pushed to the EDL — "
        "which is also the EDL policy-management and enforcement case (TC-NDR-06)."
),
    "SIM-TIM-002": (["TC-XTI-07", "TC-TH-06"], REMAP,
        "Adversary infrastructure staging — attribution and campaign context on a live case."),
    "SIM-TIM-003": (["TC-XTI-05", "TC-XTI-07", "TC-TH-01"], REMAP,
        "Sleeper-domain activation — the retro-hunt sweep when new intel lands, "
        "which is hypothesis-driven XQL hunting over historical telemetry "
        "(TC-TH-01)."
),
    "SIM-TIM-004": (["TC-XTI-07", "TC-XTI-02"], REMAP,
        "TLS-thumbprint infrastructure pivot with confidence scoring to suppress low-fidelity intel."),
    "SIM-TIM-005": (["TC-XTI-07", "TC-TH-06"], REMAP,
        "Rogue Authenticode code-signing certificate as a campaign IOC."),
    "SIM-TIM-006": (["TC-XTI-07", "TC-XTI-03", "TC-NDR-06"], REMAP,
        "Edge-VPN probing surge attributed to a scanning-infrastructure feed, then "
        "enforced at the edge via the EDL."
),
    "SIM-TIM-007": (["TC-XTI-04"], REMAP,
        "Enforcement REVERSIBILITY, the half of the intel-to-enforcement loop no "
        "other scenario touches: a benign control FQDN is promoted to the EDL and "
        "proven blocked, the operator withdraws the indicator, and the identical "
        "request is then allowed. Bound to TC-XTI-04 alone and not to the wider "
        "XTI set: TC-XTI-03 (promotion) is already evidenced by SIM-TIM-006, and "
        "this scenario's pre-flight step only CONFIRMS the promote leg landed "
        "rather than demonstrating promotion itself — claiming it here would take "
        "credit for a guard. The index scores this row 100% Automation Rate, so "
        "it is one of the few gap rows that can return a real pass."
),
    "SIM-TIM-008": (["TC-XTI-03", "TC-NDR-06", "TC-ASM-05"], REMAP,
        "Dangling-DNS record claimed and weaponized as reputation-inheriting C2 "
        "— the corpus's first T1584, closing half the coverage analyzer's only "
        "strict breach (Resource Development at 3/5). TC-XTI-03 is the honest "
        "primary and is a genuine claim rather than a re-run of SIM-TIM-006's: "
        "the indicator promoted here is MINTED LOCALLY from an ASM inventory "
        "fact plus NDR behaviour, because no feed will ever carry the "
        "customer's own subdomain. "
        "TC-XTI-09 (exposure re-ranks on active-exploitation intel) was "
        "examined and DELIBERATELY NOT BOUND, even though it is unevidenced, "
        "carries a real >=90% threshold and would have raised the coverage "
        "count. Two reasons. Its validation_class is POS, and the whole "
        "DET-by-scenario / POS-by-assertion split exists so that a scenario "
        "cannot claim a state property it only provokes. And this scenario "
        "genuinely cannot prove the queue reordered — it emits the telemetry "
        "that should cause the reorder, while the reorder itself needs a live "
        "exposure queue and the assertion substrate's pending-versus-fail "
        "taxonomy. It is a good candidate for a POS assertion under "
        "assertions/pos/, which is outside this pass's file scope."
),
    "SIM-TIM-009": (["TC-XTI-07", "TC-XTI-05", "TC-XTI-02"], REMAP,
        "Pre-weaponization capability development — a phishing-kit builder and "
        "a commodity prompt-farm tool staged before anything is pointed at the "
        "customer. Adds ZERO net-new index coverage; all three rows are already "
        "evidenced. What it adds is tactic depth: the corpus's first T1588 and "
        "second T1587, both previously at zero steps, taking Resource "
        "Development from 4 scenarios to 5 and clearing the coverage "
        "analyzer's only strict breach. The proof is a SEQUENCING proof — the "
        "TAXII ingest timestamp must strictly precede first estate contact, "
        "because an indicator ingested after the traffic is a retrospective "
        "lookup that presents identically to prevention. TC-XTI-02 is bound on "
        "the strength of a deliberate low-confidence control shipped inside "
        "the same feed batch that must be scored and not promoted."
),

    # ── CLOUD_APP ──────────────────────────────────────────────────────────
    # OAuth-grant governance binds to UC-ITDR (the IdP surface, correct SKU)
    # and to UC-DLP-11 for the data-movement half. See the SKU-gap note in
    # index-gaps-v2.2.md — the price book carries no Cloud App Security line.
    "SIM-CLOUD-001": (["TC-ITDR-03", "TC-DLP-11"], REMAP,
        "Okta risky OAuth drive-scope grant — IdP-brokered SaaS data access."),
    "SIM-CLOUD-002": (["TC-ITDR-03", "TC-DLP-11"], REMAP,
        "Microsoft admin-consent-required scope request."),
    "SIM-CLOUD-003": (["TC-DLP-11", "TC-AES-07", "TC-ITDR-03"], REMAP,
        "Google full-mailbox scope with offline token replay risk."),
    "SIM-CLOUD-004": (["TC-ITDR-03", "TC-DLP-11"], REMAP,
        "Cross-provider OAuth grant rotation across Okta, Microsoft, and Google."),
    "SIM-CLOUD-005": (["TC-CITH-11", "TC-DLP-04", "TC-TH-05"], REMAP,
        "The benign-OAuth control run exists to measure false-positive rate — the "
        "same measurement TC-TH-05 scores for smart-scoring hunt results."
),
    "SIM-CLOUD-006": (["TC-DLP-11", "TC-DSPM-04"], REMAP,
        "Post-consent token abuse driving a Salesforce Bulk API mass export."),
    "SIM-CLOUD-007": (["TC-IR-07", "TC-SOAR-02", "TC-DLP-11"], REMAP,
        "Auto-revoke of OAuth tokens on connected-app abuse — closed-loop response."),
    "SIM-CLOUD-008": (["TC-DLP-11", "TC-AES-07", "TC-DSPM-04"], REMAP,
        "M365 storage exfiltration with mailbox enumeration by an Azure app."),
    "SIM-CLOUD-009": (["TC-DLP-11", "TC-DSPM-04"], REMAP,
        "SharePoint and OneDrive enumeration by a consented application."),
    "SIM-CLOUD-010": (["TC-DLP-11", "TC-ITDR-03", "TC-AES-07", "TC-DSPM-04"], REMAP,
        "A compromised vendor's already-consented integration pivots into the "
        "tenant. Adds NOTHING to the coverage count — every bound row is already "
        "evidenced — and that is stated in the scenario header rather than left "
        "for someone to discover. Its value is threat currency: T1199 had ONE "
        "step in the entire corpus before it, against a 2026 supply-chain "
        "finding that risk moved from vulnerable code to misused trusted "
        "connectivity."),

    # ── AI_ACCESS ──────────────────────────────────────────────────────────
    # NET-NEW: the detection capability is honestly DLP-shaped, but UC-DLP
    # gates on the Endpoint DLP SKU while these validate Cortex AI Access
    # Security. Primary refs are the nearest honest capability match so the
    # corpus stays strict-clean; proposed v2.3 TCs are emitted alongside.
    "SIM-AIACC-001": (["TC-DLP-11", "TC-DLP-01"], NETNEW,
        "Source-code paste to public ChatGPT. DLP-shaped, but AI Access Security is its own product line."),
    "SIM-AIACC-002": (["TC-DLP-11", "TC-SSCAN-01"], NETNEW,
        "AWS key leaked to the Anthropic API — secret egress over an unsanctioned AI channel."),
    "SIM-AIACC-003": (["TC-DLP-11", "TC-DLP-05"], NETNEW,
        "High-volume Gemini prompt burst — behavioral, against a per-user baseline."),
    "SIM-AIACC-004": (["TC-AIRS-07", "TC-DLP-11"], NETNEW,
        "Jailbreak prompt fingerprinting on the egress path rather than at the model."),
    "SIM-AIACC-005": (["TC-DLP-11"], NETNEW,
        "Cross-provider rotation to evade single-provider policy."),
    "SIM-AIACC-006": (["TC-CITH-04", "TC-DLP-11"], NETNEW,
        "Token jacking — a stolen LLM provider key replayed from off-baseline "
        "infrastructure for unauthorized compute. Direction is the INVERSE of "
        "SIM-AIACC-001..005: those detect secrets leaking OUT to a provider, "
        "this detects a leaked provider key being USED. Primary is TC-CITH-04 "
        "because the claim is signature-independent behavioural detection — the "
        "traffic is well-formed TLS to a legitimate provider carrying a valid "
        "key, so there is nothing to signature. The honest home is a proposed "
        "v2.3 UC-AIACC row, which is out of scope for this pass."),

    # ── BROWSER ────────────────────────────────────────────────────────────
    # NET-NEW for the same reason: Prisma Browser is not the Endpoint DLP SKU.
    "SIM-BROWSER-001": (["TC-DLP-01", "TC-DLP-11"], NETNEW,
        "Credential paste into an untrusted origin — web-channel data exposure from the browser."),
    "SIM-BROWSER-002": (["TC-AES-06", "TC-DLP-01"], NETNEW,
        "Drive-by download from a phishing page — link-payload detection at the browser."),
    "SIM-BROWSER-003": (["TC-SCA-01", "TC-DLP-11"], NETNEW,
        "Sideloaded risky Chrome extension — third-party component risk in the browser."),
    "SIM-BROWSER-004": (["TC-DLP-11", "TC-DLP-01"], NETNEW,
        "Cross-origin SaaS copy-paste — sanctioned-to-unsanctioned data movement."),
    "SIM-BROWSER-005": (["TC-DLP-11", "TC-DLP-05"], NETNEW,
        "Screen capture of a sensitive SaaS page — a channel endpoint DLP cannot see."),
    "SIM-BROWSER-006": (["TC-AIRS-07", "TC-AGTX-07"], NETNEW,
        "Web-based indirect prompt injection against a browsing AI agent."),

    # ── KOI ────────────────────────────────────────────────────────────────
    # NET-NEW under the existing UC-AEPS (Agentic Endpoint Security) — that is
    # the correct SKU home, but its three TCs are AUT/PLT capability rows, so
    # the threat cases themselves do not exist in the index.
    "SIM-KOI-001": (["TC-SCA-01", "TC-AGTX-11"], NETNEW,
        "Typosquat MCP server installed by Claude Desktop — agentic supply chain has no index TC."),
    "SIM-KOI-002": (["TC-AIRS-07", "TC-AGTX-11"], NETNEW,
        "Hidden prompt injection in an MCP tool response — injection is real, the MCP trust boundary is not indexed."),
    "SIM-KOI-003": (["TC-SCA-01"], NETNEW,
        "Backdoored PyPI package consumed by an agent. TC-SCA-01 scores known CVEs, not malicious packages."),
    "SIM-KOI-004": (["TC-SCA-01"], NETNEW,
        "Malicious VS Code extension escalating permissions — developer-endpoint supply chain."),
    "SIM-KOI-005": (["TC-AIRS-07", "TC-AGTX-11"], NETNEW,
        "Malicious Claude skill carrying hidden instructions."),
    "SIM-KOI-006": (["TC-AIRS-07", "TC-AGTX-07", "TC-AGTX-11"], NETNEW,
        "MCP runtime tool-response poisoning — the connect-time versus runtime trust gap."),
    "SIM-KOI-007": (["TC-AGTX-11", "TC-AGTX-07"], NETNEW,
        "MCP sampling abuse — server-initiated inference for quota theft and covert tasking."),
    "SIM-KOI-008": (["TC-SCA-01", "TC-SSCAN-01"], NETNEW,
        "Shai-Hulud self-replicating npm worm — a preinstall hook spawning a detached child."),

    # ── DLP ────────────────────────────────────────────────────────────────
    # First DLP-plane scenarios. Refs below match each scenario's own authored
    # uc_ref/tc_ref/tc_refs exactly (verified against the YAML, not pattern-
    # matched from the id) — all bind DET-class TC-DLP-* rows except SIM-DLP-005,
    # which the index itself classifies POS (TC-DLP-07). Four of five land on
    # TC-DLP-01/02/11, already evidenced elsewhere (EDR/NDR/MP/AIACC/BROWSER/
    # CLOUD_APP) — depth, not breadth. TC-DLP-07 had no scenario or assertion
    # binding it before this pass; because it is POS-class it does not move the
    # DET/HNT evidenced count, but it does close a previously-open whole-index row.
    "SIM-DLP-001": (["TC-DLP-01"], REMAP,
        "USB mass-storage exfiltration of seeded customer PII — the canonical "
        "email/web-upload/USB channel case TC-DLP-01 describes. TC-DLP-01 is "
        "already evidenced (SIM-EDR-009, SIM-NDR-005/006/012, SIM-MP-022, "
        "SIM-AIACC-001, SIM-BROWSER-001/002/004); this adds a device-channel proof."),
    "SIM-DLP-002": (["TC-DLP-01", "TC-DLP-02"], REMAP,
        "Encrypted-archive staging before exfil — bulk copy, compress, encrypt "
        "mirrors TC-DLP-02's staged-exfiltration correlation case, with TC-DLP-01 "
        "primary per the scenario's own binding. Both refs are already evidenced "
        "(TC-DLP-02 by SIM-MP-003/022); this is a second, endpoint-native proof."),
    "SIM-DLP-003": (["TC-DLP-11"], REMAP,
        "Unsanctioned SaaS web upload of confidential financial data — the "
        "sanctioned-vs-shadow-IT SaaS case TC-DLP-11 describes. Already evidenced "
        "extensively (SIM-CLOUD-001..010, SIM-AIACC-001..006, SIM-BROWSER-*); "
        "this is a DLP-native depth proof, not new coverage."),
    "SIM-DLP-004": (["TC-DLP-02"], REMAP,
        "Cross-channel correlation: endpoint local staging fused with network "
        "exfiltration — a second, purpose-built instance of TC-DLP-02's staging "
        "correlation claim alongside SIM-MP-003/022. Depth, not breadth."),
    "SIM-DLP-005": (["TC-DLP-07"], REMAP,
        "Clipboard and print-buffer PHI harvesting — the scenario's own tc_ref "
        "binds TC-DLP-07, the compliance-driven data-controls (HIPAA/PCI/GDPR) "
        "row, which the index classifies POS (posture/policy assertion, not a "
        "detection — S-14 applies). TC-DLP-07 had zero prior scenario or "
        "assertion evidence: this is the first proof reaching it, but because "
        "it is POS-class it does NOT move the DET/HNT evidenced count — only "
        "the whole-index open-row count."),
}


# ---------------------------------------------------------------------------
# PLT assertion pack — assertions/plt/*.yml -> index binding.
#
# Deliberately a SEPARATE dict from CROSSWALK, and deliberately NOT merged into
# the `index TCs evidenced` line the report prints. Those two decisions are the
# same decision: an assertion is AUTHORED evidence, not PROVEN evidence, and a
# PLT row only becomes proven when an AssertionRun against a real tenant returns
# a pass or a fail. Folding these four into the scenario number would move
# 84/266 to 88/266 on the strength of four YAML files, which is the exact
# inflation the pack exists to avoid. They are reported on their own line, with
# proven held at zero until a tenant says otherwise.
#
# assertion_id -> (tc_refs, headline mechanism, what it deliberately does NOT prove)
# ---------------------------------------------------------------------------

PLT_ASSERTION_CROSSWALK: dict[str, tuple[list[str], str, str]] = {
    "PLT-IR-008": (["TC-IR-08"],
        "Ingestion round-trip: three structurally dissimilar sources emitted with one "
        "canary planted twice per record; normalized-field read-back is the claim, "
        "raw-body read-back is the not-normalized/not-landed discriminator.",
        "Which ingestion door carried the records — Broker VM, native collector and "
        "forwarder are indistinguishable from the query side."),
    "PLT-ERV-001": (["TC-ERV-01"],
        "Same round-trip widened to six source families spanning container, SaaS, "
        "network, cloud control-plane, endpoint/identity and IdP.",
        "The BVM/Marketplace/native-connector provenance, and coverage of the "
        "CUSTOMER's stack — the six families are the ones CortexSim emits."),
    "PLT-ITDR-006": (["TC-ITDR-06"],
        "One canonical principal planted into three identity providers' own principal "
        "fields; a single query returns the cross-provider timeline for one human.",
        "That CIE merged them into one entity OBJECT (no read API), and resolution "
        "across DIFFERENT identifiers for the same human."),
    "PLT-XDL-001": (["TC-XDL-01"],
        "Field-level normalization fidelity as a ratio over the records the platform "
        "landed, measured against the emitter's own ground truth. The pack's only "
        "index row carrying a real number (95%), so its pass is not clamped.",
        "The 1,100+ integration / 50-source / petabyte breadth claims, and the "
        "query-ready-within-15-minutes leg."),
}

# PLT index rows examined and NOT bound, with the reason. Kept in the crosswalk
# because an honest gap list is the other half of an honest coverage number.
PLT_NOT_BOUND: dict[str, str] = {
    "TC-NDR-05": "needs a genuinely THIRD-PARTY network sensor shape; every network "
                 "emitter in the engine is first-party PAN-OS, so the test case's "
                 "distinctive claim cannot be exercised.",
    "TC-XTI-06": "needs 12 months of resident data. A POV tenant three weeks old "
                 "fails for its age, which is indistinguishable from a retention "
                 "misconfiguration through XQL alone — false red.",
    "TC-XDL-04": "'<60 second' is a WALL-CLOCK query-latency bar. The substrate has "
                 "no wall-clock probe, and measuring it engine-side folds emitter, "
                 "collector and ingest delay into the platform's number.",
    "TC-XDL-05": "same aged-data problem as TC-XTI-06 plus a wall-clock 15-minute bar.",
    "TC-SIEM-01": "hot/warm/cold tier residency is not exposed by any query surface; "
                  "lookback depth is a proxy, and a proxy is not a measurement.",
    "TC-PGE-02": "a differential RBAC probe needs TWO credentials in one evaluation; "
                 "the substrate hands a probe one runner.",
    "TC-ITDR-04": "its distinctive claim IS the Marketplace connector and its sync "
                  "cadence, neither of which any read API exposes. The residual "
                  "(two IdP datasets normalize) is already PLT-ITDR-006's claim, and "
                  "binding both would double-count one piece of work.",
    "TC-TH-04": "Marketplace content-pack provenance is not exposed by any read API.",
    "TC-ERV-01+": "the remaining 35 PLT rows need a Cortex Cloud REST client the "
                  "engine does not have, billing/consumption data no API exposes, or "
                  "an autonomous agent action that belongs to the AUT class.",
}


# ---------------------------------------------------------------------------
# POS assertion pack — assertions/pos/k8s/*.yml -> index binding.
#
# Same discipline as PLT_ASSERTION_CROSSWALK, and the same two decisions: an
# assertion is AUTHORED evidence, not PROVEN, and it is reported on its own line
# and NEVER folded into the `index TCs evidenced` number (which walks Scenario
# rows only). Only TC-KSPM-03 is a NET-NEW binding — TC-CSPM-02 is already
# authored by POS-CSPM-001 against the AWS cspm module, so POS-CSPM-004 adds
# breadth (a Kubernetes resource class) and moves the coverage number by ZERO.
# Reporting that split is the whole point; the K8s pass has repeatedly chosen an
# honest smaller number over an inflated one.
#
# assertion_id -> (tc_refs, headline mechanism, what it deliberately does NOT prove)
# ---------------------------------------------------------------------------

POS_ASSERTION_CROSSWALK: dict[str, tuple[list[str], str, str]] = {
    "POS-KSPM-003": (["TC-KSPM-03"],
        "Admission enforcement as a REFUSE-is-pass check: kubernetes_audit_logs "
        "403 creates in cortexsim- namespaces are the passing evidence (chk-01), "
        "and the compliant SIM-CDR-001 control being ADMITTED (chk-02) stops a "
        "block-everything controller scoring green. The fixture is the GENERATED "
        "manifests of the privileged CDR scenarios (SIM-CDR-003/-012), not a "
        "hand-written parallel set that could drift.",
        "Attribution to Cortex Cloud's admission controller specifically (PSA "
        "produces an identical 403 shape; the namespaces opt out of PSA so the "
        "denial is provably a cluster-scoped webhook, but Cortex-vs-Kyverno needs "
        "the webhook name confirmed), the sanctioned-exception audit clause (a "
        "write), and enforcement latency."),
    "POS-CSPM-004": (["TC-CSPM-02"],
        "In-cluster arm of the planted-misconfiguration recall: the three gated "
        "workloads (SIM-CDR-003/-004/-012) surface in cortex_cloud_posture, "
        "joined on OUR cortexsim.io/posture-finding label, never the product's "
        "finding vocabulary.",
        "COVERAGE — TC-CSPM-02 is already authored by POS-CSPM-001, so this is "
        "BREADTH not coverage and moves the number by zero. Also: framework "
        "attribution, severity, remediation text, and false positives (recall "
        "only)."),
}

# POS index rows examined during the K8s pass and NOT bound, with the reason —
# the honest other half of the coverage number (see index-gaps §4a / §2e of the
# K8s content triage).
POS_NOT_BOUND: dict[str, str] = {
    "TC-KSPM-01": "asset discovery + deployment→replicaset→pod→container lineage "
                  "names Cortex Cloud Inventory (/api/v1/inventory) as its source — "
                  "the REST-probe refusal, not a fixture gap. A Deployment fixture "
                  "makes its lineage claim checkable IF a REST probe is ever built.",
    "TC-KSPM-02": "CIS Kubernetes Benchmark control-id attribution: the "
                  "control-id FIELD name on cortex_cloud_posture is unknown on top "
                  "of the dataset name, so authoring it now yields a permanent "
                  "pending. Ranked below the others until confirmed on a live tenant.",
    "TC-CDR-05": "agentless workload protection is a two-population comparison and "
                 "both populations land in the same dataset — the check would "
                 "measure one thing while claiming two.",
    "TC-CIEM-01": "net-effective permissions names IAM/resource-policy/SCP/boundary "
                  "constructs by name; Kubernetes RBAC is a different permission "
                  "algebra and does not make the AWS-shaped row reachable.",
}


# Proposed v2.3 test cases for the NET-NEW resolutions, grouped by the UC that
# should carry them. UC-AEPS already exists (correct SKU for agentic endpoint);
# UC-BROWSER and UC-AIACC are proposed net-new use cases.
PROPOSED_TCS: list[dict[str, str]] = [
    # UC-AIACC — Cortex AI Access Security (proposed new UC)
    {"tc_id": "TC-AIACC-01", "uc_id": "UC-AIACC", "ucs_id": "UCS-AIACC-01",
     "use_case": "AI Access Security", "scenario": "Sensitive Data Egress to Public AI",
     "title": "Validate detection of source code and sensitive data pasted into public AI providers",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "XSIAM (XQL: dataset=panw_ngfw_traffic_raw)",
     "scenario_id": "SIM-AIACC-001",
     "rationale": "AI Access Security brokers egress to AI providers; UC-DLP gates on the Endpoint DLP SKU."},
    {"tc_id": "TC-AIACC-02", "uc_id": "UC-AIACC", "ucs_id": "UCS-AIACC-01",
     "use_case": "AI Access Security", "scenario": "Secret Egress to AI Provider",
     "title": "Validate detection of credentials and API keys transmitted to an AI provider endpoint",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "XSIAM (XQL: dataset=panw_ngfw_traffic_raw)",
     "scenario_id": "SIM-AIACC-002",
     "rationale": "Secret-in-prompt is distinct from file-based secret scanning (TC-SSCAN-01)."},
    {"tc_id": "TC-AIACC-03", "uc_id": "UC-AIACC", "ucs_id": "UCS-AIACC-02",
     "use_case": "AI Access Security", "scenario": "Anomalous AI Consumption",
     "title": "Demonstrate behavioral detection of anomalous AI usage volume against a learned user baseline",
     "primary_kpi": "Detection Accuracy", "threshold": ">80%",
     "detection_source": "XSIAM (XQL: dataset=panw_ngfw_traffic_raw)",
     "scenario_id": "SIM-AIACC-003",
     "rationale": "Per-user AI consumption baselining has no analogue in UC-DLP."},
    {"tc_id": "TC-AIACC-04", "uc_id": "UC-AIACC", "ucs_id": "UCS-AIACC-02",
     "use_case": "AI Access Security", "scenario": "Jailbreak Fingerprinting at Egress",
     "title": "Validate jailbreak and prompt-attack fingerprinting on the AI egress path",
     "primary_kpi": "Detection Accuracy", "threshold": ">85%",
     "detection_source": "XSIAM (XQL: dataset=panw_ngfw_traffic_raw)",
     "scenario_id": "SIM-AIACC-004",
     "rationale": "TC-AIRS-07 detects at the model; this detects at the network broker."},
    {"tc_id": "TC-AIACC-05", "uc_id": "UC-AIACC", "ucs_id": "UCS-AIACC-02",
     "use_case": "AI Access Security", "scenario": "Provider Rotation Evasion",
     "title": "Demonstrate detection of cross-provider rotation used to evade single-provider policy",
     "primary_kpi": "Detection Accuracy", "threshold": ">80%",
     "detection_source": "XSIAM (XQL: dataset=panw_ngfw_traffic_raw)",
     "scenario_id": "SIM-AIACC-005",
     "rationale": "Evasion by rotation is only visible across providers."},

    # UC-BROWSER — Prisma Browser (proposed new UC)
    {"tc_id": "TC-BRWS-01", "uc_id": "UC-BROWSER", "ucs_id": "UCS-BROWSER-01",
     "use_case": "Enterprise Browser Security", "scenario": "Browser Credential Exposure",
     "title": "Validate detection of credential entry or paste into an untrusted web origin",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "Prisma Browser telemetry",
     "scenario_id": "SIM-BROWSER-001",
     "rationale": "Prisma Browser is a distinct SKU from Endpoint DLP; in-page context is browser-only."},
    {"tc_id": "TC-BRWS-02", "uc_id": "UC-BROWSER", "ucs_id": "UCS-BROWSER-01",
     "use_case": "Enterprise Browser Security", "scenario": "Browser-Delivered Payload",
     "title": "Validate detection of drive-by download and phishing-page payload delivery in-browser",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "Prisma Browser telemetry",
     "scenario_id": "SIM-BROWSER-002",
     "rationale": "TC-AES-06 covers the mail-delivered link; this covers direct browsing."},
    {"tc_id": "TC-BRWS-03", "uc_id": "UC-BROWSER", "ucs_id": "UCS-BROWSER-02",
     "use_case": "Enterprise Browser Security", "scenario": "Risky Extension Governance",
     "title": "Validate detection of sideloaded or over-permissioned browser extensions",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "Prisma Browser telemetry",
     "scenario_id": "SIM-BROWSER-003",
     "rationale": "Extension permission risk has no index test case."},
    {"tc_id": "TC-BRWS-04", "uc_id": "UC-BROWSER", "ucs_id": "UCS-BROWSER-02",
     "use_case": "Enterprise Browser Security", "scenario": "Cross-Origin Data Movement",
     "title": "Demonstrate control of copy-paste and upload between sanctioned and unsanctioned SaaS origins",
     "primary_kpi": "Detection Accuracy", "threshold": ">85%",
     "detection_source": "Prisma Browser telemetry",
     "scenario_id": "SIM-BROWSER-004",
     "rationale": "Origin-to-origin movement is invisible to endpoint DLP."},
    {"tc_id": "TC-BRWS-05", "uc_id": "UC-BROWSER", "ucs_id": "UCS-BROWSER-02",
     "use_case": "Enterprise Browser Security", "scenario": "Screen-Capture Control",
     "title": "Validate detection and control of screen capture over a sensitive SaaS page",
     "primary_kpi": "Detection Accuracy", "threshold": ">85%",
     "detection_source": "Prisma Browser telemetry",
     "scenario_id": "SIM-BROWSER-005",
     "rationale": "Screen capture is a browser-enforced control with no index analogue."},
    {"tc_id": "TC-BRWS-06", "uc_id": "UC-BROWSER", "ucs_id": "UCS-BROWSER-03",
     "use_case": "Enterprise Browser Security", "scenario": "Agentic Browsing Risk",
     "title": "Validate detection of indirect prompt injection served to a browsing AI agent",
     "primary_kpi": "Detection Accuracy", "threshold": ">85%",
     "detection_source": "Prisma Browser telemetry",
     "scenario_id": "SIM-BROWSER-006",
     "rationale": "Agentic browsing is a net-new attack surface for the browser plane."},

    # UC-AEPS — Agentic Endpoint Security (EXISTING UC, correct SKU; its three
    # current TCs are AUT/PLT capability rows, so the threat cases are net-new)
    {"tc_id": "TC-AEPS-04", "uc_id": "UC-AEPS", "ucs_id": "UCS-AEPS-02",
     "use_case": "Agentic Endpoint Security", "scenario": "Agentic Supply-Chain Compromise",
     "title": "Validate detection of typosquat or malicious MCP server installation on an agentic endpoint",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "XSIAM (XQL: dataset=xdr_data)",
     "scenario_id": "SIM-KOI-001",
     "rationale": "UC-AEPS holds the correct SKU but carries no threat test cases."},
    {"tc_id": "TC-AEPS-05", "uc_id": "UC-AEPS", "ucs_id": "UCS-AEPS-02",
     "use_case": "Agentic Endpoint Security", "scenario": "Agentic Supply-Chain Compromise",
     "title": "Validate detection of malicious packages and extensions consumed by developer or agent tooling",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "XSIAM (XQL: dataset=xdr_data)",
     "scenario_id": "SIM-KOI-003",
     "rationale": "TC-SCA-01 scores known CVEs in dependencies, not deliberately malicious packages."},
    {"tc_id": "TC-AEPS-06", "uc_id": "UC-AEPS", "ucs_id": "UCS-AEPS-03",
     "use_case": "Agentic Endpoint Security", "scenario": "MCP Runtime Trust Boundary",
     "title": "Validate detection of MCP tool-response poisoning and runtime instruction injection",
     "primary_kpi": "Detection Accuracy", "threshold": ">85%",
     "detection_source": "XSIAM (XQL: dataset=xdr_data)",
     "scenario_id": "SIM-KOI-006",
     "rationale": "The connect-time versus runtime trust gap (CVE-2025-49596/-54136) is not indexed."},
    {"tc_id": "TC-AEPS-07", "uc_id": "UC-AEPS", "ucs_id": "UCS-AEPS-03",
     "use_case": "Agentic Endpoint Security", "scenario": "MCP Runtime Trust Boundary",
     "title": "Demonstrate detection of MCP sampling abuse for quota theft and covert tasking",
     "primary_kpi": "Detection Accuracy", "threshold": ">85%",
     "detection_source": "XSIAM (XQL: dataset=xdr_data)",
     "scenario_id": "SIM-KOI-007",
     "rationale": "Server-initiated inference inverts the client/server trust assumption."},
    {"tc_id": "TC-AEPS-08", "uc_id": "UC-AEPS", "ucs_id": "UCS-AEPS-02",
     "use_case": "Agentic Endpoint Security", "scenario": "Self-Propagating Package Worm",
     "title": "Validate detection of self-replicating package worms via install-hook process behavior",
     "primary_kpi": "Detection Accuracy", "threshold": ">90%",
     "detection_source": "XSIAM (XQL: dataset=xdr_data)",
     "scenario_id": "SIM-KOI-008",
     "rationale": "Shai-Hulud-class worms propagate through install hooks, not through a CVE."},
]


# ---------------------------------------------------------------------------
# Machinery
# ---------------------------------------------------------------------------


def load_index():
    tc = {r["tc_id"]: r for r in csv.DictReader(open(os.path.join(IDX, "tc_index_v2.2.csv")))}
    spec = {r["tc_id"]: r for r in csv.DictReader(open(os.path.join(IDX, "detection_spec_v2.2.csv")))}
    lib = list(csv.DictReader(open(os.path.join(IDX, "scenario_library_v2.2.csv"))))
    return tc, spec, lib


def scenario_files() -> dict[str, str]:
    """scenario_id -> path, for the loadable corpus only."""
    import yaml

    out = {}
    for p in sorted(glob.glob(os.path.join(REPO, "scenarios", "**", "*.yml"), recursive=True)):
        if os.path.basename(p).startswith("_"):
            continue
        if any(x in p for x in ("/probes/", "/packages/", "/campaigns/")):
            continue
        with open(p, "r", encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
        if isinstance(d, dict) and "scenario_id" in d and "plane" in d:
            out[d["scenario_id"]] = p
    return out


def derive_payload(tc_refs: list[str], lib) -> str:
    """A payload IS its bound TC set, so the payload this scenario instantiates
    is the one whose set best overlaps the authored refs.

    Rank by whether the payload carries the PRIMARY ref, then by matched-ref
    count, then prefer the tighter payload. Scoring the ratio alone is wrong: a
    one-TC payload matching a single ref would beat a payload matching two,
    which picks noise over signal. And without the primary-ref term, a tight
    payload holding a secondary ref outranks the payload the scenario is
    actually an instance of.
    """
    primary = tc_refs[0] if tc_refs else ""
    best, best_key = "", (0, 0, 0)
    for row in lib:
        bound = set(row["bound_tc_ids"].split())
        if not bound:
            continue
        hits = len(bound & set(tc_refs))
        if not hits:
            continue
        key = (1 if primary in bound else 0, hits, -len(bound))
        if key > best_key:
            best, best_key = row["pov_scenario_id"], key
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--emit", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not (args.report or args.emit or args.apply):
        args.report = True

    tc, spec, lib = load_index()
    files = scenario_files()

    missing = sorted(set(files) - set(CROSSWALK))
    extra = sorted(set(CROSSWALK) - set(files))
    if missing:
        print(f"ERROR: {len(missing)} scenario(s) absent from the crosswalk: {missing}", file=sys.stderr)
    if extra:
        print(f"ERROR: {len(extra)} crosswalk row(s) name no scenario: {extra}", file=sys.stderr)

    bad = sorted({r for refs, _, _ in CROSSWALK.values() for r in refs if r not in tc})
    if bad:
        print(f"ERROR: crosswalk references non-existent test cases: {bad}", file=sys.stderr)
    if missing or extra or bad:
        return 1

    rows = []
    import yaml

    for sid in sorted(CROSSWALK):
        refs, resolution, rationale = CROSSWALK[sid]
        path = files[sid]
        with open(path, "r", encoding="utf-8") as fh:
            y = yaml.safe_load(fh)
        pid = derive_payload(refs, lib)
        primary = tc[refs[0]]
        rows.append({
            "scenario_id": sid,
            "plane": y.get("plane", ""),
            "old_uc_ref": y.get("uc_ref", ""),
            "old_tc_ref": y.get("tc_ref", ""),
            "new_uc_ref": primary["ucs_id"],
            "new_tc_ref": refs[0],
            "new_tc_refs": " ".join(refs),
            "new_uc_name": primary["use_case"],
            "new_tc_name": primary["test_case_title"],
            "pov_scenario_id": pid,
            "validation_class": spec.get(refs[0], {}).get("validation_class", ""),
            "index_tier": primary["differentiation_tier"],
            "old_moat_tier": y.get("moat_tier", ""),
            "resolution": resolution,
            "rationale": rationale,
        })

    if args.report:
        print(f"scenarios: {len(rows)}   crosswalk rows: {len(CROSSWALK)}")
        print("resolution:", dict(Counter(r["resolution"] for r in rows).most_common()))
        evidenced = {x for refs, _, _ in CROSSWALK.values() for x in refs}
        det = {k for k, v in spec.items() if v["validation_class"] in ("DET", "HNT")}
        print(f"index TCs evidenced: {len(evidenced)}/266  "
              f"(DET/HNT {len(evidenced & det)}/{len(det)})")
        print("payload resolved:", sum(1 for r in rows if r["pov_scenario_id"]),
              "| unresolved:", sum(1 for r in rows if not r["pov_scenario_id"]))
        print("tier drift (S-13):",
              sum(1 for r in rows if r["old_moat_tier"] and r["old_moat_tier"] != r["index_tier"]))
        print("posture-class primary (S-14):",
              sum(1 for r in rows if r["validation_class"] in ("POS", "PLT", "AUT")))
        print("proposed v2.3 TCs:", len(PROPOSED_TCS),
              dict(Counter(p["uc_id"] for p in PROPOSED_TCS).most_common()))

        # Reported on its own line, never folded into the number above. An
        # assertion existing is AUTHORED, not PROVEN — proven needs an
        # AssertionRun against a real tenant returning pass or fail.
        plt_total = sum(1 for v in spec.values() if v["validation_class"] == "PLT")
        plt_bound = {r for refs, _, _ in PLT_ASSERTION_CROSSWALK.values() for r in refs}
        bad_plt = sorted(r for r in plt_bound if r not in tc)
        wrong_class = sorted(
            r for r in plt_bound
            if spec.get(r, {}).get("validation_class") not in ("", "PLT")
        )
        if bad_plt:
            print(f"ERROR: PLT assertion crosswalk references non-existent test "
                  f"cases: {bad_plt}", file=sys.stderr)
            return 1
        if wrong_class:
            print(f"ERROR: PLT assertion crosswalk binds non-PLT rows: "
                  f"{wrong_class}", file=sys.stderr)
            return 1
        print(f"PLT assertions: {len(PLT_ASSERTION_CROSSWALK)} artifact(s) "
              f"-> {len(plt_bound)}/{plt_total} PLT rows AUTHORED "
              f"| 0/{plt_total} PROVEN (no tenant run recorded) "
              f"| {len(PLT_NOT_BOUND)} documented non-bindings")

        # The K8s POS pack, on its own line for the same reason. Only NET-NEW POS
        # rows (ones no Scenario and no other assertion already reached) are the
        # honest coverage delta; a row already authored elsewhere is breadth, not
        # coverage, and is called out as such.
        pos_total = sum(1 for v in spec.values() if v["validation_class"] == "POS")
        pos_bound = {r for refs, _, _ in POS_ASSERTION_CROSSWALK.values() for r in refs}
        bad_pos = sorted(r for r in pos_bound if r not in tc)
        wrong_pos = sorted(
            r for r in pos_bound
            if spec.get(r, {}).get("validation_class") not in ("", "POS")
        )
        if bad_pos:
            print(f"ERROR: POS assertion crosswalk references non-existent test "
                  f"cases: {bad_pos}", file=sys.stderr)
            return 1
        if wrong_pos:
            print(f"ERROR: POS assertion crosswalk binds non-POS rows: "
                  f"{wrong_pos}", file=sys.stderr)
            return 1
        # A POS row is net-new only if no scenario in CROSSWALK already evidences
        # it; TC-CSPM-02 is evidenced by SIM-CSPM-001, so it is breadth here.
        pos_net_new = sorted(pos_bound - evidenced)
        pos_breadth = sorted(pos_bound & evidenced)
        print(f"POS assertions (K8s pack): {len(POS_ASSERTION_CROSSWALK)} artifact(s) "
              f"-> {len(pos_bound)}/{pos_total} POS rows AUTHORED "
              f"| 0/{pos_total} PROVEN (no tenant run recorded) "
              f"| net-new {pos_net_new} | breadth-not-coverage {pos_breadth} "
              f"| {len(POS_NOT_BOUND)} documented non-bindings")

    if args.emit:
        cw = os.path.join(OUT, "crosswalk-v2.2.csv")
        with open(cw, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print("wrote", cw)

        pt = os.path.join(OUT, "proposed-tc-v2.3.csv")
        with open(pt, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(PROPOSED_TCS[0]))
            w.writeheader()
            w.writerows(PROPOSED_TCS)
        print("wrote", pt)

    if args.apply:
        changed = 0
        for r in rows:
            path = files[r["scenario_id"]]
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            new = _rewrite_refs(text, r)
            if new != text:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
                changed += 1
        print(f"rewrote {changed} scenario file(s)")

    return 0


def _rewrite_refs(text: str, row: dict) -> str:
    """Rewrite uc_ref/tc_ref in place and insert tc_refs + pov_scenario_id
    immediately after tc_ref, preserving surrounding formatting and comments."""
    refs = row["new_tc_refs"].split()

    text = re.sub(r"^uc_ref:.*$", f"uc_ref: {row['new_uc_ref']}", text, count=1, flags=re.M)

    block = [f"tc_ref: {row['new_tc_ref']}"]
    if len(refs) > 1:
        block.append("tc_refs:")
        block.extend(f"  - {r}" for r in refs)
    if row["pov_scenario_id"]:
        block.append(f"pov_scenario_id: {row['pov_scenario_id']}")

    # Drop any previously-applied block so re-running is idempotent.
    text = re.sub(r"^tc_refs:\n(?:  - .*\n)+", "", text, flags=re.M)
    text = re.sub(r"^pov_scenario_id:.*\n", "", text, flags=re.M)
    text = re.sub(r"^tc_ref:.*$", "\n".join(block), text, count=1, flags=re.M)

    # uc_name / tc_name are the human-readable labels FOR those refs. Leaving
    # them describing the old test case is the same silent-wrongness the refs
    # had — re-key them from the index row.
    text = re.sub(r'^uc_name:.*$', f'uc_name: {_q(row["new_uc_name"])}', text, count=1, flags=re.M)
    text = re.sub(r'^tc_name:.*$', f'tc_name: {_q(row["new_tc_name"])}', text, count=1, flags=re.M)
    return text


def _q(s: str) -> str:
    """Double-quote a YAML scalar, escaping embedded quotes and backslashes."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


if __name__ == "__main__":
    raise SystemExit(main())
