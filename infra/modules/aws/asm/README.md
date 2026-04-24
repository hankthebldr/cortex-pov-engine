---
name: asm
description: Intentionally exposed public attack surface (multi-service EC2 + public S3 website + vulnerable app) for Cortex ASM discovery validation.
providers: [aws]
required_params: [project_name]
optional_params: [exposed_instance_type]
dependencies: [base]
---

# asm (AWS)

Provisions a **deliberately exposed attack surface** that Cortex ASM should discover and enumerate during a POV. The goal is to give the DC a predictable target set so they can validate that Cortex ASM catches all the common exposure classes customers worry about.

## What's exposed

### Multi-service EC2 (public IP)

One Ubuntu 22.04 host in the public subnet running several intentionally-misconfigured services:

| Port | Service | Finding |
|------|---------|---------|
| 80 | nginx | Directory listing enabled, fake admin/config/backup dirs with bait files |
| 443 | nginx TLS | Self-signed + RSA-1024 + broad cipher list |
| 2222 | SSH | Non-standard port, password auth enabled |
| 6379 | Redis | Bound to 0.0.0.0, no auth, protected-mode off |
| 9001 | gocortexbrokenbank | Intentionally vulnerable CI/CD app |
| 9200 | Fake Elasticsearch | Returns ES v1.7.0 banner (known-vulnerable version) |

### Public S3 website

A separate S3 bucket with `s3-website` hosting enabled, public policy, and an index.html. Demonstrates `*.s3-website-<region>.amazonaws.com` domain discovery.

## Content installed

Vulnerable apps: gocortexbrokenbank.
Honeypots: cowrie, dionaea, conpot, glastopf, honeytrap (if DC wants to deploy decoys alongside exposed targets).
Surface discovery tools: shells (bug-bounty toolkit).

## Validation workflow

1. Apply the bundle. Cortex ASM scans should discover the public IP and enumerate open ports within its scan cycle.
2. Verify Cortex ASM surfaces:
   - Each of the 7 exposed ports
   - The weak TLS cert (short RSA key, self-signed)
   - The non-standard SSH port
   - The fake Elasticsearch banner (CVE-matched to ES 1.7.0)
   - The directory-listing nginx root
   - The public S3 website endpoint
3. Use Cortex ASM attack-path recommendations to confirm the tool not only finds assets but prioritizes risk correctly.

## Cost + safety notes

- Single t3.small EC2 + 1 GB S3: pennies per hour.
- Self-signed certs won't be trusted; intentional.
- gocortexbrokenbank is safe to run — it's by-design vulnerable but not a real banking app.
- Destroy via `terraform destroy` when the POV ends. The Torque TTL should enforce this automatically.
