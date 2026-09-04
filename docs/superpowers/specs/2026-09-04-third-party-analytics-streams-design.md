# Third-Party Data Streams & Analytics — feature brief

Status: **design** (awaiting review) · 2026-09-04
Branch: `feature/analytics-third-party-streams` (cut from `dev` @ `2e92ba0`)
Source of truth for scope: <https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-data-source>

---

## 0 · The split

`Traffic / EAL` is currently one console destination over two families of plugin
that answer different questions and are validated differently. They separate:

| | **Traffic EAL** | **3rd-Party Data Streams & Analytics** |
|---|---|---|
| What it produces | live network behaviour — real sockets, real DNS, real TLS | shape-true **log records** POSTed to a collector |
| Detected by | NDR / NGFW / behavioural BIOCs on observed traffic | **Analytics + ABIOC detectors** keyed on dataset fields |
| Proof it worked | the packet left the host | the record landed in the dataset **and** the detector fired |
| Existing plugins | `c2_http_beacon`, `dns_tunnel_exfil`, `stratum_tcp_connect`, `smb_rpc_sweep`, `bulk_https_exfil`, `ftp_egress`, `ssh_egress` | `cloud_audit_emitter`, `azure_audit_emitter`, `k8s_audit_emitter`, `m365_activity_emitter`, `ad_windows_emitter`, `ngfw_eal_emitter`, `cloud_storage_compute_emitter`, `idp_signin_emulator` |

Both families already exist in `core/eal_simulator/plugins/`; the second has a
spine at `core/eal_simulator/analytics_emitter.py`. **This is a surfacing and
coverage problem, not a new subsystem** — the split is real in the code and
invisible in the console.

## 1 · Counted coverage against the documented catalogue

The docs index lists **34 data sources**. Mapping our emitters to it by the
dataset each writes:

| Data source (doc) | Dataset we emit | State |
|---|---|---|
| AWS Audit Log | `cloud_audit_logs` | ✅ |
| Gcp Audit Log | `cloud_audit_logs` | ✅ |
| Azure Audit Log | `msft_azure_audit` | ✅ |
| Azure SignIn Log | `msft_azure_ad_signin` | ✅ |
| Kubernetes Audit Logs | `kubernetes_audit_logs` | ✅ |
| Office 365 Audit | `msft_o365_audit` | ✅ |
| Google Workspace Audit Logs | `google_workspace_audit` | ✅ |
| Okta | `okta_sso` | ✅ |
| Windows Event Collector | `msft_windows_security` | ✅ |
| PAN Firewall EAL Logs | `panw_ngfw_traffic_raw` | ✅ |
| PAN Firewall traffic Logs | `panw_ngfw_traffic_raw` | ✅ |
| AzureAD · Google Workspace Authentication · Microsoft 365 Emails | partial reuse | ⚠️ |
| AzureAD Audit Log · Okta Audit Log · Microsoft Graph Logs | — | ❌ |
| Box · DropBox · Duo · OneLogin · PingOne · Idira | — | ❌ |
| PAN threat Logs · Url Logs · GlobalProtect · Platform Alerts | — | ❌ |
| Third-Party Alerts · Third-Party Firewalls · Third-Party VPNs | — | ❌ |
| Health Monitoring Data | — | ❌ |
| XDR Agent · XDR Agent (XTH) | not a stream — the beacon/identity harness owns this | n/a |

**~11 of 32 addressable sources have an emitter. The gap is ~21.**

That count is the deliverable of this section: it is the first time this
repo can state its analytics coverage against the vendor's own catalogue
rather than against its own plugin list.

## 2 · The ingestion mechanism

Records are POSTed to an operator-supplied HTTP collector / Broker VM and land
in a dataset named for the source, following XSIAM's `<vendor>_<product>_raw`
convention. This is the same collector-POST pattern `idp_signin_emulator` and
`email_emitter` already use, and it is why the existing family works at all
without a tenant integration.

**The whole difficulty is in one sentence, and it is the operator's own:**

> the critical part is the event traffic matches the analytics detectors and sensors

A record that lands in the right dataset with plausible-looking fields but does
**not** satisfy the detector's actual predicates produces silence. Silence in a
POV report reads as *"Cortex missed it"* — the manufactured false negative this
whole engine exists to avoid, reproduced at the exact layer meant to prove
coverage. Shape-plausible is not detector-true, and only the second one counts.

## 3 · What that implies for the work

1. **Per-source, the detector predicates are the spec — not the log format.**
   For each data source, the unit of work is: which analytics alerts exist for
   it, what fields each keys on, and what values make it fire. The vendor page
   groups alerts by source precisely so this can be enumerated.
2. **Every emitter needs a negative control.** If the same emitter can produce a
   record that must NOT fire the detector, the detector is being exercised
   rather than merely fed. Without it we cannot tell "fired correctly" from
   "fires on anything".
3. **Delivery accounting already exists and must be reused.** `delivery.py`
   counts only 2xx as delivered (12-code taxonomy, `delivery_verdict`). New
   emitters inherit it; none may count a POST that did not land.
4. **Coverage must be reported against the vendor catalogue**, not against our
   own plugin count — otherwise the number grows while the gap stays.

## 4 · Console change

`Traffic / EAL` becomes two destinations:

- **Traffic / EAL** — the network-behaviour plugins, unchanged in function.
- **Data Streams** — the analytics log-streamer family, listing each supported
  data source, its dataset, its delivery verdict, and its coverage state against
  the catalogue (including the sources with **no** emitter, which must be
  visible rather than absent — an unlisted gap reads as no gap).

## 5 · Open questions for the operator

1. **Which sources first?** The gap is ~21. Ranking should come from POV demand,
   not alphabetical order. Third-Party Firewalls / VPNs / Alerts look highest
   value because they are the generic buckets most customers land in.
2. **Do we have a tenant to verify against?** Everything here is unprovable
   without one: `tenant-verified` is 0, and an emitter whose detector has never
   been observed firing is authored, not proven. This feature makes that gap
   bigger, not smaller, until a tenant answers.
3. **Negative controls per detector** — is the operator willing to author the
   "must not fire" case alongside each "must fire" case? It roughly doubles the
   authoring cost and is the only thing that makes the coverage claim real.

## 6 · Non-goals

- Replacing the identity harness for `XDR Agent` sources. Endpoint process
  telemetry comes from a real beacon executing a real binary; it is not a log
  shape we can POST.
- Claiming coverage from an emitter that has never been observed to fire its
  detector against a live tenant.
