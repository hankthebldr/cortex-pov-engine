# Identity lab design — sources

Two research passes ran on 2026-11-11, one on the official Palo Alto Networks documentation
and one on lab build options. Each cited the pages it fetched. The main session then
re-fetched the claims the design rests on. This page keeps the two apart: **re-checked**
means the main session read the page text itself; **reported** means a research pass fetched
it and the main session did not re-read it.

## Re-checked by the main session

| Claim | Page | What it says |
|---|---|---|
| `pan_dss_raw` is the CIE dataset and exists only after CIE setup | https://cortex-docs.paloaltonetworks.com/cortex-xsiam/reference-and-developer-docs/cortex-agentix-xql/get-started-with-xql/datasets-and-presets.md | "Active Directory via Cloud Identity Engine — `pan_dss_raw` … you need to set up a Cloud Identity Engine. Otherwise, you will not have a pan_dss_raw dataset." |
| Agent-collected Windows events land in `microsoft_windows_raw` | same page | "Windows event logs via Cortex XDR Windows agents — `microsoft_windows_raw`" |
| CIE supports on-prem AD and OpenLDAP only; agent host is Windows Server 2016–2025, ≥ 8 GB RAM, ≥ 10 GB disk; SSL decryption must be off | https://docs.paloaltonetworks.com/identity/release-notes/cloud-identity-engine/cloud-identity-engine-release-notes-welcome/cloud-identity-engine-release-notes-system-requirements | "On-premises directories (Microsoft Active Directory and OpenLDAP)"; "You must disable SSL decryption on the firewall for traffic to or from the agent host. Windows Server 2016, 2019, 2022, or 2025 … 8 GB or more of RAM." |
| *Possible Kerberoasting attack* needs the XDR agent with XTH, activation 14 days, training 30 days | https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/possible-kerberoasting-attack | "Activation Period 14 Days · Training Period 30 Days · Required Data: Requires all of the following: XDR Agent, XDR Agent with eXtended Threat Hunting (XTH)" |
| deathstar has no Windows endpoint | Cortex MCP `get_filtered_endpoints platform=windows`, 2026-11-11 | `total_count: 0` |
| 0 cards read `pan_dss_raw`; 9 read `msft_windows_security` | `grep` over `detection_scanner/ttps/*.json` at `dev` 3f3e84b | counts as stated |

`get_tenant_info`, and `get_assets` filtered to identity assets, both timed out twice on
2026-11-11, so deathstar's entitlements and identity inventory were **not** read.

## Reported by the docs research pass (Palo Alto Networks)

* Cloud Identity agent install and placement ("on a supported Active Directory or
  OpenLDAP-based directory server"; separate host from a User-ID agent):
  https://docs.paloaltonetworks.com/identity/cloud-identity-engine/identify-users-and-devices-with-cie/choose-directory-type/configure-an-on-premises-directory/install-the-cloud-identity-agent
* Agent configuration (LDAPS 636 default, 389, Global Catalog not to be used, regional
  `agent-directory-sync.<region>` endpoints, up to 20 domains, HA by duplicate agents):
  https://docs.paloaltonetworks.com/identity/cloud-identity-engine/identify-users-and-devices-with-cie/choose-directory-type/configure-an-on-premises-directory/configure-the-cloud-identity-agent
* Network allowlist (GoDaddy CRL/OCSP on 80, `certs.godaddy.com:443`, App-ID
  `paloalto-cloud-identity`, proxies from agent 1.7.1):
  https://docs.paloaltonetworks.com/identity/activation-and-onboarding/get-started-with-the-cloud-identity-engine/plan-the-cloud-identity-engine-deployment/configure-your-network-to-allow-cloud-identity-engine-traffic
* Certificate authentication (3-month certificates, auto-renewal from 1.5.0):
  https://docs.paloaltonetworks.com/identity/cloud-identity-engine/identify-users-and-devices-with-cie/choose-directory-type/configure-an-on-premises-directory/authenticate-cloud-identity-agent-and-the-cloud-identity-engine
* AD attributes CIE collects:
  https://docs.paloaltonetworks.com/identity/cloud-identity-engine/manage-the-cloud-identity-engine/cloud-identity-engine-attributes/cloud-identity-engine-attributes-active-directory
* Sync timing (incremental every 5 minutes, full weekly):
  https://docs.paloaltonetworks.com/identity/cloud-identity-engine/cloud-identity-engine-instances/synchronize-cloud-identity-engine-instances
* CIE licensing (free): https://docs.paloaltonetworks.com/identity/activation-and-onboarding/get-started-with-the-cloud-identity-engine/cloud-identity-engine-licensing
* XSIAM CIE set-up (same region; required for Identity Analytics detection rules):
  https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/set-up-cloud-identity-engine
* Identity Analytics enablement and the 30-endpoint / 2-week baseline:
  https://cortex-docs.paloaltonetworks.com/cortex-xsiam/onboard-cortex-xsiam/deployment-steps/cortex-xsiam-analytics/enable-the-analytics-engine-and-identity-analytics
* ITDR getting started (Identity Profile, agent 9.1+, 9.3+ for Conditional Access and AD-SPM):
  https://cortex-docs.paloaltonetworks.com/cortex-xsiam/detect-investigate-and-respond-to-threats/identity-threat-module-itdr/get-started-with-itdr
* Audit policy (identity events marked "DCs only"; 4662 needs a SACL):
  https://cortex-docs.paloaltonetworks.com/cortex-xsiam/reference-and-developer-docs/reference/microsoft-windows-security-auditing-setup/enable-security-auditing-event-ids/enable-security-auditing-event-ids-with-gpo
  and …/enable-auditing-access-to-ad-domain-objects-4662
* Broker VM Windows Event Collector applet:
  https://cortex-docs.paloaltonetworks.com/cortex-xsiam/configure-cortex-xsiam/cortex-xsiam-data-sources/generic-on-premise-data-collectors/broker-vm-data-collector-applets/activate-windows-event-collector
* `get_tenant_info` entitlement fields: https://cortex-docs.paloaltonetworks.com/xsiam-api/cortex-platform/system-management
* Licence matrices: https://cortex-docs.paloaltonetworks.com/cortex-xsiam/learn-about-cortex-xsiam/cortex-xsiam-product-licenses
* The detector table in the design, §8.1: one page per detector under
  `https://cortex-docs.paloaltonetworks.com/analytics-alerts/alerts-by-name/<slug>`
  (29 pages fetched).

## Reported by the build-options research pass

* GOAD (GPL-3.0; providers VirtualBox, VMware, Azure, AWS, Proxmox, Ludus; no libvirt):
  https://github.com/Orange-Cyberdefense/GOAD and https://orange-cyberdefense.github.io/GOAD/providers/
* Community libvirt fork (1 commit, 43 behind): https://github.com/pomcom/GOAD-libvirt
* Ludus (Debian 12/13 or Proxmox 8/9 only): https://docs.ludus.cloud/docs/intro
* DetectionLab unmaintained since 2023-01-01: https://github.com/clong/DetectionLab
* Windows Server 2022/2025 evaluations (180 days; online activation within 10 days):
  https://www.microsoft.com/en-us/evalcenter/evaluate-windows-server-2022 and …-2025
* Windows Server 2025 OEM use terms (copying limits):
  https://www.microsoft.com/content/dam/microsoft/usetm/documents/windows-server/2025-datacenter-and-standard/oem/UseTerms_OEM_WindowsServer2025_DatacenterAndStandard_English.pdf
* Packer QEMU builder: https://developer.hashicorp.com/packer/integrations/hashicorp/qemu/latest/components/builder/qemu
* Unattend driver paths: https://learn.microsoft.com/en-us/windows-hardware/customize/desktop/unattend/microsoft-windows-pnpcustomizationswinpe-driverpaths
* Ansible over SSH on Windows (2.18+, OpenSSH 7.9+): https://docs.ansible.com/ansible/latest/os_guide/windows_ssh.html
* Samba audit logging (text/JSON, not a Windows Security log): https://wiki.samba.org/index.php/Setting_up_Audit_Logging
* Gitea generic registry (immutable versions, 409 on re-upload): https://docs.gitea.com/usage/packages/generic
* Traefik streaming-upload 408 report: https://github.com/traefik/traefik/issues/12668
* Sysinternals licence (no publishing): https://learn.microsoft.com/en-us/sysinternals/license-terms
* virtio-win (BSD-3, attestation-signed): https://github.com/virtio-win/virtio-win-pkg-scripts/blob/master/README.md
* Rubeus (BSD-3, no published binaries): https://github.com/GhostPack/Rubeus
* Impacket licence: https://github.com/fortra/impacket/blob/master/LICENSE

## Unverified

* The Windows Server evaluation ISO's own EULA on internal mirroring (not fetched).
* What an unactivated evaluation does after day 10.
* Whether the CIE agent is better placed on a member server than on the DC (permitted on
  both; no best-practice statement found).
* Whether the CIE agent host must be domain-joined.
* `pan_dss_raw`'s field names (the canary query cannot be fixed until they are captured).
* Whether the Analytics baseline is per tenant.
* What `identity_threat_expiration` and `purchased_xth` mean exactly (undocumented fields).
* For detectors that list "XDR Agent" alone, whether the data comes from the DC's agent, the
  endpoints' agents, or both.
* Whether detectors with no detection module fire without CIE.
* Server 2025 Kerberos / RC4 defaults and their effect on Kerberoasting realism.
* Build times other than Ludus's "a few hours" for a full GOAD.
* Exact Ansible collection versions to mirror.
