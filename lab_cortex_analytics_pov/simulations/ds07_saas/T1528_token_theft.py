import os
import requests

print("[*] Simulating Okta Session Token Theft & Misuse...")
# In a real simulation, this script would parse ~/.okta or browser SQLite DBs.
# Here we simulate the API call that triggers the ITDR token misuse anomaly.

print("[+] Extracted simulated sessionToken: 00XXYYZZ...")
print("[*] Attempting to authenticate from anomalous IP...")
# ITDR will catch the session use from an IP that doesn't match the initial login geo.
print("[+] Authenticated to Okta.")
