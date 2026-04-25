#!/bin/bash
# T1021.002 - SMB Lateral Movement
echo "[*] Simulating SMB Lateral Movement..."
# Use smbclient to attempt connection to the C$ share of an internal windows host
# This, combined with Agent telemetry and EAL, should trigger an Enhanced Lateral Movement alert.
smbclient //10.0.60.10/C$ -U "Administrator%Password123!" -c "ls" || true
echo "[+] SMB attempt dispatched."
