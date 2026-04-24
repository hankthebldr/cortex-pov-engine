---
name: itdr
description: Windows Active Directory domain (DC + workstations) for identity threat detection scenarios — Kerberoast, DCSync, Pass-the-Hash, AS-REP Roast
providers: [aws]
required_params: [project_name]
optional_params: [ad_domain_name, ad_netbios_name, dc_instance_type, workstation_instance_type, workstation_count]
dependencies: [base]
---

# itdr (AWS)

Provisions a complete Windows Active Directory lab in the base VPC's private subnets:

- **1 Domain Controller** — Windows Server 2022, auto-promotes to a new forest on first boot
- **1+ Workstations** — Windows Server 2022 Core (AWS lacks Windows 11 AMIs in most regions), auto-joins the domain after the DC is up
- **AD seeding** — 50 regular users, 5 misconfigured service accounts (Kerberoast bait with SPN + weak `Summer2024` password), and 1 DA-equivalent account with `DoesNotRequirePreAuth` set (AS-REP Roast bait)
- **Domain admin password** — generated randomly if not supplied, stored in SSM SecureString at `/cortexsim/<project_name>/ad-admin-password`

## Detection scenarios unlocked

| Technique | MITRE | Content tool | Seeded bait |
|-----------|-------|--------------|-------------|
| Kerberoasting | T1558.003 | `Rubeus.exe kerberoast` / `impacket-GetUserSPNs` | 5 SPN-bearing svc accounts w/ weak pwd |
| AS-REP Roasting | T1558.004 | `impacket-GetNPUsers` | `helpdesk-admin` w/ DoesNotRequirePreAuth |
| DCSync | T1003.006 | `impacket-secretsdump` | Domain Admin credential available |
| Pass-the-Hash | T1550.002 | `impacket-psexec -hashes` | NTLM hashes usable across domain |
| Golden Ticket | T1558.001 | `Rubeus.exe golden` | krbtgt hash dumpable via DCSync |
| BloodHound enum | T1087.002 | `sharphound.exe -c All` | Full AD graph available |
| LDAP reconnaissance | T1087.002 | `impacket-GetADUsers` | 50+ users visible |

## Content installed

Credential attack tools: Impacket, Rubeus, Certipy, Responder.
AD mapping: SharpHound, BloodHound, bloodhound-python.
Identity simulation: msInvader, adversary-emulation-framework, impact.
Dumping tools: Mimikatz, pypykatz.

Content installs on the **jumpbox**, not on Windows hosts. The jumpbox targets the DC via WinRM/PowerShell Remoting or LDAP to execute techniques.

## Accessing the domain

After `terraform apply`, retrieve the Domain Admin password and connect from the jumpbox:

```bash
aws ssm get-parameter \
  --name $(terraform output -raw ad_admin_password_ssm_path) \
  --with-decryption --query Parameter.Value --output text

# From jumpbox — sample Impacket attack
DC_IP=$(terraform output -raw dc_private_ip)
DOMAIN=$(terraform output -raw ad_domain_name)
impacket-GetUserSPNs $DOMAIN/Administrator:'<password>' -dc-ip $DC_IP -request
```

## Boot time

The DC takes ~15 minutes to fully provision (AD-DS install + reboot + user seeding). Workstations take another ~10 minutes after the DC is ready (they retry domain-join in a 30-attempt loop).

## Security notes

- Password stored encrypted in SSM; decrypt only on-demand.
- Hosts have no public IPs — reachable only via the jumpbox security group.
- The `helpdesk-admin` DA-equivalent account is **intentionally misconfigured** for AS-REP Roast demos. Destroy the environment after the POV completes.
