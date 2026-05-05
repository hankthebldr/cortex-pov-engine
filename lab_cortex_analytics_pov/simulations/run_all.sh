#!/bin/bash
# Cortex POV - Main Simulation Runner
# Scenario: Compromised Linux Host + Network EAL + ITDR

set -e

echo "[*] Starting Cortex POV Simulation..."
echo "[*] Ensure you are running this from the Attacker Node (10.0.50.100)"

sleep 2

# Phase 1: Initial Access / Execution
echo "[+] Phase 1: DS-01 Agent (Unix Shell)"
./ds01_agent/T1059_004_unix_shell.sh
sleep 60 # Wait for CDL flush

# Phase 2: Command & Control
echo "[+] Phase 2: DS-02 NGFW (C2 Beaconing)"
./ds02_ngfw/T1071_001_https_c2.sh
sleep 60

# Phase 3: EAL Network Lateral Movement
echo "[+] Phase 3: DS-02 NGFW EAL (Recon & Lateral)"
./ds02_ngfw/T1046_internal_port_scan.sh
sleep 30
./ds02_ngfw/T1021_002_smb_lateral.sh
sleep 60

# Phase 4: Identity Theft & SaaS Exfiltration
echo "[+] Phase 4: DS-07 SaaS ITDR (Token Theft & Exfil)"
python3 ./ds07_saas/T1528_token_theft.py
sleep 30
python3 ./ds07_saas/T1530_mass_download.py

echo "[*] Simulation complete. Please check XSIAM for Incident Stitching."
