---
name: tim
description: Threat Intelligence Management — TAXII 2.1 server (mocktaxii) + fake C2 HTTP endpoint + planted Route53 IOC domains. Feeds Cortex TIM and produces network traffic that matches its own IOCs.
providers: [aws]
required_params: [project_name]
optional_params: [tim_instance_type]
dependencies: [base]
---

# tim (AWS)

Provisions the threat intelligence plane: a STIX/TAXII 2.1 server that XSIAM can subscribe to, a fake C2 HTTP endpoint that endpoints can beacon to, and a Route53 private zone with IOC-style subdomain records that resolve to the fake C2.

## What gets deployed

### TIM host (public EC2)

- **Port 9000**: mocktaxii — TAXII 2.1-compliant server, serves STIX indicators to XSIAM
- **Port 8000**: fake C2 — responds 200 to any GET/POST with a signature JSON body, logs all requests to `/var/log/fake-c2.log` so the DC can see which endpoints "called home"

### Route53 private zone with planted IOCs

Five subdomains inside `<project>-tim.internal` resolve to the fake C2 IP:
- `c2-beacon.*`
- `exfil-drop.*`
- `payload-delivery.*`
- `dga-1a2b3c.*`
- `cryptominer-pool.*`

These names match patterns typical IOC feeds tag as malicious. When an endpoint scenario queries one of them (via DNS) and connects to the resolved IP, Cortex TIM should fire an IOC match alert, and NDR should fire a network-level detection on the same session — **stitched into one incident**.

## Content installed

Threat intel sources: mocktaxii, Unit42 timely threat intel, pan-unit42 public tools.
IOC samples: MalwareSamples feed, theZoo, malware-souk.
YARA rules: yara-rules/rules, reversinglabs-yara, awesome-yara meta-list.
Malware source code: vxunderground malware source.

## Stitching with NDR and EDR

The TIM module pairs naturally with the `ndr` and `edr` modules:

1. EDR scenario uses `curl http://c2-beacon.<project>-tim.internal:8000/checkin` on a target host
2. TIM fires IOC alert (domain + IP match against STIX feed)
3. NDR logs the outbound session (VPC Flow Log + NGFW log to collector)
4. EDR logs the `curl` process with parent lineage
5. XSIAM correlates all three into a single C2 incident

See `scenarios/multi_plane/SIM-MP-001` for the stitching walkthrough.

## Configuration in XSIAM

After apply, point the customer XSIAM TIM connector at:

```
TAXII Server URL: $(terraform output -raw taxii_endpoint_url)
```

mocktaxii serves a default STIX 2.1 collection with indicators for the planted C2 IP and the IOC-style domains.

## Cost + safety

Single t3.small, one public IP, one private Route53 zone. All traffic is scoped to the deployed VPC (Route53 zone is private). Destroy via `terraform destroy` — mocktaxii and the fake C2 are both stateless.
