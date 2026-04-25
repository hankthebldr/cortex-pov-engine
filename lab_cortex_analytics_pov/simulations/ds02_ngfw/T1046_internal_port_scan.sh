#!/bin/bash
# T1046 - Internal Port Scan
echo "[*] Simulating fast internal network recon..."
# We assume we are running this on the compromised linux host
# Scanning standard internal management ports to trigger EAL Internal Port Scan
nmap -p 22,445,3389,5985,5986 10.0.60.0/24 -T4 --max-retries 1 --open
echo "[+] Network recon complete."
