---
name: telemetry-replay
description: Content-only module — pre-recorded EVTX/PCAP/JSON attack datasets plus replay tooling. No cloud resources, installs on the jumpbox for parser/correlation validation without executing live attacks.
providers: [aws, gcp, azure]
required_params: []
optional_params: []
dependencies: [base]
---

# telemetry-replay

Content-only module. Selecting this produces **no additional cloud resources** — it only adds entries to the jumpbox content installer that pull down a curated library of pre-recorded attack telemetry (EVTX, PCAP, JSON logs) plus replay tooling.

## Why this matters

Not every POV objective requires executing real attacks. Some customers want to validate:
- **XSIAM parsers** — can it correctly ingest and parse all of the log formats a forensic dataset contains?
- **Correlation rules** — does XQL successfully join events across an extensive, realistic timeline?
- **Detection coverage at scale** — does the detection set fire on 7.9M events the way it's expected to?

For these objectives, replaying pre-recorded datasets is faster, safer, and more reproducible than generating live signal. This module gives the DC the canonical datasets plus tooling to replay them.

## Content installed

### Attack-mapped EVTX datasets

- **EVTX-ATTACK-SAMPLES** (`sbousseaden/EVTX-ATTACK-SAMPLES`) — raw Windows event logs keyed to ATT&CK
- **EVTX-to-MITRE-Attack** (`mdecrevoisier/EVTX-to-MITRE-Attack`) — 270+ IOC EVTX samples per tactic/technique
- **hayabusa-sample-evtx** (`Yamato-Security/hayabusa-sample-evtx`) — curated for timeline generation + Sigma testing

### Realistic enterprise simulation datasets

- **mordor** (`OTRF/mordor`) — datasets with malicious + benign telemetry for analytic validation
- **cyber_simulation** (`gregdiy/cyber_simulation`) — 7.9M-log multi-user pivot campaign for signal-to-noise testing

### ML-oriented datasets

- **MalbehavD-V1** (`mpasco/MalbehavD-V1`) — malware/benign PE feature extractions
- **awesome-malware-benign-datasets** (`0xh3xa/awesome-malware-benign-datasets`) — meta-list (BODMAS, EMBER)
- **mh-100k-dataset** (`Malware-Hunter/MH-100K-dataset`) — 101,975 Android malware + benign samples
- **dikedataset** (`iosifache/DikeDataset`) — formatted malicious + benign PE files
- **markov-malware-images** (`julismail/Markov`) — grayscale image representations for visual ML

### EDR coverage comparison

- **EDR-Telemetry** (`tsale/EDR-Telemetry`) — empirical CSV maps of what commercial EDRs actually capture

### Replay tooling

- **chainsaw** (`WithSecureLabs/chainsaw`) — rapid EVTX triage with Sigma rules applied in-place
- **hayabusa** (indirect via `hayabusa-sample-evtx`) — timeline generator
- **sigma-rules-crawler** (`SimoneCagol/sigma-rules-crawler`) — outputs Elastic NDJSON for rapid SIEM ingestion
- **tcpreplay examples** (`appneta/tcpreplay`) — PCAP replay onto test networks

## Usage from the jumpbox

Each dataset lands under `/opt/cortexsim/content/telemetry-replay/<repo-name>/`. SimCore's content loader surfaces them in the tool panel as `type: content`. The DC selects a dataset and uses the replay tooling to push it at XSIAM:

```bash
# Example: replay EVTX into XSIAM via chainsaw (applies Sigma detections)
chainsaw hunt /opt/cortexsim/content/telemetry-replay/evtx-attack-samples/ \
  --sigma /opt/cortexsim/content/base/sigma/rules/windows/ \
  --output xsiam-forwarding.jsonl

# Push the resulting JSONL to XSIAM HTTP collector
curl -X POST -H 'Content-Type: application/json' \
  --data-binary @xsiam-forwarding.jsonl \
  http://<xsiam-collector>/http-logs
```

## Why no cloud resources?

Datasets are heavy to stage but cheap to store on the jumpbox. Running them doesn't require standing up additional infra — the compute already exists on the jumpbox provisioned by the `base` module. Keeping this module content-only avoids paying for idle infrastructure during replay-focused POVs.
