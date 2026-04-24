terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

locals {
  name_prefix = var.project_name
  common_tags = merge({
    Project   = var.project_name
    Module    = "itdr"
    ManagedBy = "cortexsim-iac-generator"
  }, var.tags)
}

# ----- Domain Admin password (generated if not supplied) ------------------

resource "random_password" "ad_admin" {
  count            = var.ad_admin_password == "" ? 1 : 0
  length           = 24
  special          = true
  override_special = "!@#%^&*()-_=+"
}

locals {
  ad_admin_password = var.ad_admin_password != "" ? var.ad_admin_password : random_password.ad_admin[0].result
}

resource "aws_ssm_parameter" "ad_admin_password" {
  name        = "/cortexsim/${local.name_prefix}/ad-admin-password"
  description = "Domain Administrator password for CortexSim ITDR AD domain"
  type        = "SecureString"
  value       = local.ad_admin_password
  tags        = local.common_tags
}

# ----- Windows AMIs -------------------------------------------------------

data "aws_ami" "windows_server_2022" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["Windows_Server-2022-English-Full-Base-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

data "aws_ami" "windows_11" {
  most_recent = true
  owners      = ["amazon"]

  # Windows 11 client AMIs are not always available in every region.
  # Falls back to Windows Server 2022 Core as a workstation stand-in
  # if no Windows 11 match — documented in README.
  filter {
    name   = "name"
    values = ["Windows_Server-2022-English-Core-Base-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ----- Security groups ----------------------------------------------------

resource "aws_security_group" "ad" {
  name        = "${local.name_prefix}-itdr-ad-sg"
  description = "CortexSim ITDR domain — DC + workstations"
  vpc_id      = var.vpc_id

  # Jumpbox → RDP/WinRM/SSH
  ingress {
    description     = "RDP from jumpbox"
    from_port       = 3389
    to_port         = 3389
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }
  ingress {
    description     = "WinRM HTTP from jumpbox"
    from_port       = 5985
    to_port         = 5985
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }
  ingress {
    description     = "WinRM HTTPS from jumpbox"
    from_port       = 5986
    to_port         = 5986
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }

  # Intra-domain (same SG) — AD replication, LDAP, Kerberos, SMB, RPC
  ingress {
    description = "AD traffic within domain"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Outbound any"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-itdr-ad-sg" })
}

# ----- Domain Controller --------------------------------------------------

resource "aws_instance" "dc" {
  ami                         = data.aws_ami.windows_server_2022.id
  instance_type               = var.dc_instance_type
  subnet_id                   = var.private_subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.ad.id]
  associate_public_ip_address = false
  get_password_data           = false

  user_data = <<-EOT
    <powershell>
    # ---- CortexSim ITDR DC bootstrap ----
    $ErrorActionPreference = "Continue"
    Start-Transcript -Path C:\cortexsim-bootstrap.log -Append

    # Set Administrator password
    net user Administrator "${local.ad_admin_password}"

    # Enable WinRM (HTTP) for Ansible/DC management from jumpbox
    winrm quickconfig -quiet
    winrm set winrm/config/service '@{AllowUnencrypted="true"}'
    winrm set winrm/config/service/auth '@{Basic="true"}'

    # Install AD DS + DNS + RSAT
    Install-WindowsFeature -Name AD-Domain-Services,DNS,RSAT-AD-Tools -IncludeManagementTools

    # Promote to DC (creates new forest)
    $SecurePwd = ConvertTo-SecureString "${local.ad_admin_password}" -AsPlainText -Force
    Install-ADDSForest `
      -DomainName "${var.ad_domain_name}" `
      -DomainNetbiosName "${var.ad_netbios_name}" `
      -SafeModeAdministratorPassword $SecurePwd `
      -InstallDns -Force -NoRebootOnCompletion

    # Schedule post-reboot user/SPN creation
    $PostReboot = @"
    Import-Module ActiveDirectory

    # ---- Seed 50 users for BloodHound / credential attack breadth ----
    \`$pwd = ConvertTo-SecureString '${local.ad_admin_password}' -AsPlainText -Force
    1..50 | ForEach-Object {
      New-ADUser -Name "user\`$_" -SamAccountName "user\`$_" -UserPrincipalName "user\`$_@${var.ad_domain_name}" ``
        -AccountPassword \`$pwd -Enabled \`$true -PassThru | Out-Null
    }

    # ---- Seed 5 misconfigured service accounts for Kerberoast ----
    # (Weak passwords + SPN set = roastable)
    foreach (\`$svc in @('sql-svc','web-svc','bkp-svc','ora-svc','app-svc')) {
      \`$weakpwd = ConvertTo-SecureString 'Summer2024' -AsPlainText -Force
      New-ADUser -Name \`$svc -SamAccountName \`$svc -UserPrincipalName "\`$svc@${var.ad_domain_name}" ``
        -AccountPassword \`$weakpwd -Enabled \`$true -PasswordNeverExpires \`$true -PassThru | Out-Null
      setspn -s "HTTP/\`$svc.${var.ad_domain_name}" \`$svc
    }

    # ---- Seed DA-equivalent user without preauth (AS-REP Roast bait) ----
    New-ADUser -Name 'helpdesk-admin' -SamAccountName 'helpdesk-admin' ``
      -UserPrincipalName 'helpdesk-admin@${var.ad_domain_name}' ``
      -AccountPassword \`$pwd -Enabled \`$true ``
      -KerberosEncryptionType RC4 -PassThru | Out-Null
    Set-ADAccountControl -Identity 'helpdesk-admin' -DoesNotRequirePreAuth \`$true
    Add-ADGroupMember -Identity 'Domain Admins' -Members 'helpdesk-admin'

    Unregister-ScheduledTask -TaskName CortexSimPostReboot -Confirm:\`$false
    "@
    $PostReboot | Out-File C:\cortexsim-post-reboot.ps1 -Encoding UTF8

    $Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-ExecutionPolicy Bypass -File C:\cortexsim-post-reboot.ps1'
    $Trigger = New-ScheduledTaskTrigger -AtStartup
    $Principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -RunLevel Highest
    Register-ScheduledTask -TaskName CortexSimPostReboot -Action $Action -Trigger $Trigger -Principal $Principal

    Stop-Transcript
    Restart-Computer -Force
    </powershell>
  EOT

  root_block_device {
    volume_size = 60
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-itdr-dc"
    Role = "domain-controller"
  })
}

# ----- Domain-joined Workstations -----------------------------------------

resource "aws_instance" "workstation" {
  count                       = var.workstation_count
  ami                         = data.aws_ami.windows_11.id
  instance_type               = var.workstation_instance_type
  subnet_id                   = var.private_subnet_ids[count.index % length(var.private_subnet_ids)]
  vpc_security_group_ids      = [aws_security_group.ad.id]
  associate_public_ip_address = false

  user_data = <<-EOT
    <powershell>
    Start-Transcript -Path C:\cortexsim-bootstrap.log -Append
    net user Administrator "${local.ad_admin_password}"

    # WinRM
    winrm quickconfig -quiet
    winrm set winrm/config/service '@{AllowUnencrypted="true"}'
    winrm set winrm/config/service/auth '@{Basic="true"}'

    # Point DNS at the DC (private IP)
    $idx = (Get-NetAdapter | Where-Object Status -eq 'Up' | Select-Object -First 1).ifIndex
    Set-DnsClientServerAddress -InterfaceIndex $idx -ServerAddresses ("${aws_instance.dc.private_ip}")

    # Wait for DC to come up, then domain-join
    $SecurePwd = ConvertTo-SecureString "${local.ad_admin_password}" -AsPlainText -Force
    $Cred = New-Object System.Management.Automation.PSCredential("${var.ad_netbios_name}\Administrator", $SecurePwd)

    $attempt = 0
    while ($attempt -lt 30) {
      try {
        Add-Computer -DomainName "${var.ad_domain_name}" -Credential $Cred -Force -ErrorAction Stop
        break
      } catch {
        Start-Sleep -Seconds 60
        $attempt++
      }
    }

    Stop-Transcript
    Restart-Computer -Force
    </powershell>
  EOT

  root_block_device {
    volume_size = 40
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-itdr-ws-${count.index}"
    Role = "domain-workstation"
  })

  depends_on = [aws_instance.dc]
}
