import time

print("[*] Simulating Mass File Download / Exfiltration via Graph API...")
print("[*] Target: M365 SharePoint / OneDrive")

# Simulate a tight loop of file downloads
for i in range(1, 101):
    print(f"    [+] GET /v1.0/me/drive/items/{i}/content")
    time.sleep(0.05)

print("[+] Mass download simulation complete (100 files).")
