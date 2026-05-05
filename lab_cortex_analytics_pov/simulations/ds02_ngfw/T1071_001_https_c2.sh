#!/bin/bash
# T1071.001 - HTTPS C2 Beaconing
echo "[*] Simulating HTTPS C2 Beaconing..."
# Start a simple while loop curl to simulate a beacon
for i in {1..5}; do
  curl -s -k "https://10.0.50.100/api/v1/poll" > /dev/null || true
  sleep 5
done
echo "[+] Beaconing simulation dispatched."
