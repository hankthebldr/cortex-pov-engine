# Installer — Plan B: Windows + WSL2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working Windows download-cradle installer — `iex (iwr -useb .../install.ps1)` — that enables WSL2, installs an Ubuntu distro, installs docker-ce inside that WSL distro, runs the same CortexSim Linux container that Plan A produces, registers a Windows service supervisor, and passes `/api/health` on the Windows host at `http://127.0.0.1:8888`.

**Architecture:** Mirror-image of Plan A's two-stage layout. Stage-1 is a small hand-auditable PowerShell bootstrap that fetches and SHA-verifies stage-2. Stage-2 is a zip payload containing: a PowerShell WSL-bootstrap script (enables the WSL2 feature, installs Ubuntu-22.04), a bash `docker-in-wsl.sh` that runs inside the WSL distro to install docker-ce, a PowerShell supervisor script that the Windows service wraps around `wsl.exe docker compose up`, and a PowerShell entrypoint that orchestrates the whole thing. The annotate library is duplicated as a PowerShell port (`annotate.ps1`) since stage-2's main entrypoint runs on the Windows side, not inside WSL.

**Tech Stack:** PowerShell 5.1+ (ships on Windows Server 2022 by default), Pester (PowerShell test framework), PSScriptAnalyzer (PS linter), WSL2, Ubuntu-22.04 WSL distro, bash/docker-ce inside WSL, Windows Service Control Manager (`sc.exe`), GitHub Actions `windows-2022` runners.

**Prerequisites:** Plan A must be complete and merged. This plan reuses the compose template (`installer/stage2/common/compose.yml.tmpl`), the manifest/release conventions, and the Dockerfile from Plan A.

---

### Task 1: Scaffolding and PowerShell tooling check

**Files:**
- Create: `installer/stage2/windows/` (directory)
- Create: `tests/installer/pester/` (directory)

- [ ] **Step 1: Create directories**

```bash
mkdir -p installer/stage2/windows tests/installer/pester
```

- [ ] **Step 2: Verify pwsh / Pester are available on the dev machine**

```bash
pwsh -Version 2>/dev/null || powershell -Command '$PSVersionTable.PSVersion'
pwsh -Command 'Get-Module -ListAvailable Pester | Select-Object -First 1 Name, Version'
```

Expected: PowerShell 5.1+ or pwsh 7.x. Pester 5.x installed. If Pester missing:

```bash
pwsh -Command 'Install-Module -Name Pester -Force -SkipPublisherCheck -Scope CurrentUser'
pwsh -Command 'Install-Module -Name PSScriptAnalyzer -Force -Scope CurrentUser'
```

- [ ] **Step 3: Add a .gitkeep to the new dirs so git tracks them**

```bash
touch installer/stage2/windows/.gitkeep tests/installer/pester/.gitkeep
git add installer/stage2/windows/.gitkeep tests/installer/pester/.gitkeep
git commit -m "chore: scaffold Windows stage-2 and Pester test dirs"
```

---

### Task 2: PowerShell annotate library — Pester test first

**Files:**
- Create: `tests/installer/pester/Annotate.Tests.ps1`

- [ ] **Step 1: Write failing Pester test**

Write to `tests/installer/pester/Annotate.Tests.ps1`:

```powershell
Describe "annotate.ps1" {
    BeforeAll {
        $script:LogFile = [System.IO.Path]::GetTempFileName()
        $env:CORTEXSIM_DEMO_MODE = "1"
        $env:CORTEXSIM_INSTALLER_RUN_ID = "test-run-123"
        $env:ANNOTATE_LOG_PATH = $script:LogFile
        . "$PSScriptRoot/../../../installer/stage2/common/annotate.ps1"
    }

    AfterAll {
        Remove-Item -Path $script:LogFile -Force -ErrorAction SilentlyContinue
    }

    BeforeEach {
        Clear-Content -Path $script:LogFile -ErrorAction SilentlyContinue
    }

    It "emits NDJSON with technique and tactic" {
        Invoke-Annotate -Technique "T1105" -Action "fetched_stage2" -Extra @{src="ghcr.io/foo:bar"}
        $line = Get-Content $script:LogFile -Raw
        $obj = $line | ConvertFrom-Json
        $obj.technique | Should -Be "T1105"
        $obj.tactic | Should -Be "command-and-control"
        $obj.action | Should -Be "fetched_stage2"
        $obj.src | Should -Be "ghcr.io/foo:bar"
    }

    It "sets technique and tactic to null when given dash" {
        Invoke-Annotate -Technique "-" -Action "installed_docker_ce"
        $obj = Get-Content $script:LogFile -Raw | ConvertFrom-Json
        $obj.technique | Should -BeNullOrEmpty
        $obj.tactic | Should -BeNullOrEmpty
        $obj.action | Should -Be "installed_docker_ce"
    }

    It "is a no-op when demo mode disabled" {
        $env:CORTEXSIM_DEMO_MODE = "0"
        Invoke-Annotate -Technique "T1105" -Action "should_not_fire"
        (Get-Content $script:LogFile -Raw -ErrorAction SilentlyContinue) | Should -BeNullOrEmpty
        $env:CORTEXSIM_DEMO_MODE = "1"
    }

    It "throws on unknown technique" {
        { Invoke-Annotate -Technique "T9999" -Action "bogus" } | Should -Throw "*unknown technique*"
    }
}
```

- [ ] **Step 2: Run and verify failure**

```bash
pwsh -Command 'Invoke-Pester -Path tests/installer/pester/Annotate.Tests.ps1'
```

Expected: FAIL — `annotate.ps1` not found.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/installer/pester/Annotate.Tests.ps1
git commit -m "test: Pester tests for PowerShell annotate library (failing)"
```

---

### Task 3: PowerShell annotate library — implement

**Files:**
- Create: `installer/stage2/common/annotate.ps1`

- [ ] **Step 1: Implement annotate.ps1**

Write to `installer/stage2/common/annotate.ps1`:

```powershell
# annotate.ps1 — structured ATT&CK-annotated install event emitter (Windows).
# Dot-source this file; call Invoke-Annotate -Technique <id> -Action <name> [-Extra @{...}].

$script:TechniqueToTactic = @{
    "T1059.001" = "execution"
    "T1059.004" = "execution"
    "T1105"     = "command-and-control"
    "T1027"     = "defense-evasion"
    "T1548.002" = "privilege-escalation"
    "T1548.003" = "privilege-escalation"
    "T1543.002" = "persistence"
    "T1543.003" = "persistence"
    "T1569.002" = "execution"
    "T1053.005" = "persistence"
}

function Invoke-Annotate {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Technique,
        [Parameter(Mandatory)][string]$Action,
        [hashtable]$Extra
    )

    if ($env:CORTEXSIM_DEMO_MODE -ne "1") { return }

    $tactic = $null
    if ($Technique -ne "-") {
        if (-not $script:TechniqueToTactic.ContainsKey($Technique)) {
            throw "annotate: unknown technique '$Technique'"
        }
        $tactic = $script:TechniqueToTactic[$Technique]
    }

    $runId = if ($env:CORTEXSIM_INSTALLER_RUN_ID) { $env:CORTEXSIM_INSTALLER_RUN_ID } else { [guid]::NewGuid().ToString() }
    $stage = if ($env:ANNOTATE_STAGE) { $env:ANNOTATE_STAGE } else { "stage2-windows" }
    $logPath = if ($env:ANNOTATE_LOG_PATH) { $env:ANNOTATE_LOG_PATH } else { "$env:ProgramData\CortexSim\install.ndjson" }

    $dir = Split-Path -Parent $logPath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    $event = [ordered]@{
        ts                = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
        installer_run_id  = $runId
        stage             = $stage
        technique         = if ($Technique -eq "-") { $null } else { $Technique }
        tactic            = $tactic
        action            = $Action
        host              = [Environment]::MachineName
        user              = [Environment]::UserName
    }

    if ($Extra) {
        foreach ($k in $Extra.Keys) { $event[$k] = $Extra[$k] }
    }

    $json = $event | ConvertTo-Json -Compress -Depth 4
    Add-Content -Path $logPath -Value $json -Encoding UTF8

    # Also emit to Windows Event Log if the source is registered
    try {
        if (-not [System.Diagnostics.EventLog]::SourceExists("CortexSim-Installer")) {
            [System.Diagnostics.EventLog]::CreateEventSource("CortexSim-Installer", "Application")
        }
        [System.Diagnostics.EventLog]::WriteEntry("CortexSim-Installer", $json, [System.Diagnostics.EventLogEntryType]::Information, 1000)
    } catch {
        # Event log write is best-effort. Don't fail the install if it doesn't work.
    }
}
```

- [ ] **Step 2: Run Pester tests and verify pass**

```bash
pwsh -Command 'Invoke-Pester -Path tests/installer/pester/Annotate.Tests.ps1 -Output Detailed'
```

Expected: all 4 tests pass.

- [ ] **Step 3: Run PSScriptAnalyzer**

```bash
pwsh -Command 'Invoke-ScriptAnalyzer -Path installer/stage2/common/annotate.ps1 -Severity Warning'
```

Expected: no diagnostics of severity Warning or higher.

- [ ] **Step 4: Commit**

```bash
git add installer/stage2/common/annotate.ps1
git commit -m "feat: PowerShell annotate library for Windows stage-2 events"
```

---

### Task 4: Stage-1 PowerShell bootstrap — Pester test first

**Files:**
- Create: `tests/installer/pester/Bootstrap.Tests.ps1`
- Create (later): `installer/bootstrap/install.ps1`

- [ ] **Step 1: Write failing Pester test**

Write to `tests/installer/pester/Bootstrap.Tests.ps1`:

```powershell
Describe "install.ps1 bootstrap" {
    BeforeAll {
        $script:Bootstrap = "$PSScriptRoot/../../../installer/bootstrap/install.ps1"
    }

    It "exposes Install-CortexSim function after dot-sourcing" {
        . $script:Bootstrap
        Get-Command Install-CortexSim -ErrorAction Stop | Should -Not -BeNullOrEmpty
    }

    It "Install-CortexSim -Help prints usage" {
        $out = pwsh -NoProfile -Command ". '$script:Bootstrap'; Install-CortexSim -Help" 2>&1 | Out-String
        $out | Should -Match "Usage:"
        $out | Should -Match "-Offline"
        $out | Should -Match "-DemoMode"
        $out | Should -Match "-Version"
    }

    It "Get-CortexSimOSArch returns windows-amd64 on amd64 host" {
        . $script:Bootstrap
        $arch = Get-CortexSimOSArch
        $arch | Should -BeIn @("windows-amd64", "windows-arm64")
    }

    It "Install-CortexSim throws on sha256 mismatch when -LocalStage2 used" {
        $tmp = New-Item -ItemType Directory -Path "$env:TEMP\cortexsim-test-$(Get-Random)" -Force
        Set-Content -Path (Join-Path $tmp "stage2-windows.zip") -Value "fake"
        $manifest = @{
            version = "vtest"
            artifacts = @{
                "stage2-windows.zip" = @{ sha256 = "0000000000000000000000000000000000000000000000000000000000000000" }
            }
        } | ConvertTo-Json -Depth 4
        Set-Content -Path (Join-Path $tmp "manifest.json") -Value $manifest
        { & pwsh -NoProfile -Command ". '$script:Bootstrap'; Install-CortexSim -LocalStage2 '$tmp'" 2>&1 } | Should -Throw
        Remove-Item -Recurse -Force $tmp
    }
}
```

- [ ] **Step 2: Run and verify failure**

```bash
pwsh -Command 'Invoke-Pester -Path tests/installer/pester/Bootstrap.Tests.ps1'
```

Expected: FAIL.

- [ ] **Step 3: Commit**

```bash
git add tests/installer/pester/Bootstrap.Tests.ps1
git commit -m "test: Pester tests for Windows stage-1 bootstrap (failing)"
```

---

### Task 5: Stage-1 PowerShell bootstrap — implement

**Files:**
- Create: `installer/bootstrap/install.ps1`

- [ ] **Step 1: Implement install.ps1**

Write to `installer/bootstrap/install.ps1`:

```powershell
<#
.SYNOPSIS
    CortexSim stage-1 bootstrap (Windows).
.DESCRIPTION
    Downloads and SHA-verifies stage-2, then invokes it. Designed to run via:
      iex (iwr -useb https://.../install.ps1); Install-CortexSim
#>

$ErrorActionPreference = "Stop"

function Get-CortexSimOSArch {
    $arch = $env:PROCESSOR_ARCHITECTURE
    switch ($arch) {
        "AMD64" { return "windows-amd64" }
        "ARM64" { return "windows-arm64" }
        default { throw "unsupported arch: $arch" }
    }
}

function Get-CortexSimFileSha256 {
    param([string]$Path)
    (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
}

function Install-CortexSim {
    [CmdletBinding()]
    param(
        [string]$Version = "latest",
        [string]$Offline,
        [string]$LocalStage2,
        [switch]$DemoMode,
        [string]$ReleaseUrl = "https://github.com/hankthebldr/cortexsim/releases",
        [switch]$Help
    )

    if ($Help) {
        Write-Host @"
Usage: Install-CortexSim [options]

Options:
  -Version <VERSION>        Release tag to install (default: latest)
  -Offline <PATH>           Path to offline bundle .zip (skips network fetch)
  -LocalStage2 <DIR>        Use a local stage-2 directory (dev); expects manifest.json
  -DemoMode                 Emit ATT&CK-annotated NDJSON telemetry during install
  -ReleaseUrl <URL>         Override release URL
  -Help                     Show this help

Examples:
  iex (iwr -useb .../install.ps1); Install-CortexSim
  iex (iwr -useb .../install.ps1); Install-CortexSim -DemoMode
  Install-CortexSim -Offline .\cortexsim-windows-amd64.zip
"@
        return
    }

    $env:CORTEXSIM_DEMO_MODE = if ($DemoMode) { "1" } else { "0" }
    if (-not $env:CORTEXSIM_INSTALLER_RUN_ID) {
        $env:CORTEXSIM_INSTALLER_RUN_ID = [guid]::NewGuid().ToString()
    }

    $osArch = Get-CortexSimOSArch
    $stage2Name = "stage2-windows.zip"
    $workdir = Join-Path $env:TEMP "cortexsim-install-$([guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $workdir -Force | Out-Null

    try {
        # Locate manifest and stage-2 archive
        if ($LocalStage2) {
            $manifestPath = Join-Path $LocalStage2 "manifest.json"
            $stage2Path   = Join-Path $LocalStage2 $stage2Name
        } elseif ($Offline) {
            Expand-Archive -Path $Offline -DestinationPath $workdir -Force
            $manifestPath = Join-Path $workdir "manifest.json"
            $stage2Path   = Join-Path $workdir $stage2Name
        } else {
            $base = if ($Version -eq "latest") { "$ReleaseUrl/latest/download" } else { "$ReleaseUrl/download/$Version" }
            $manifestPath = Join-Path $workdir "manifest.json"
            $stage2Path   = Join-Path $workdir $stage2Name
            Invoke-WebRequest -UseBasicParsing -Uri "$base/manifest.json" -OutFile $manifestPath
            Invoke-WebRequest -UseBasicParsing -Uri "$base/$stage2Name"  -OutFile $stage2Path
        }

        # SHA verify
        $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
        $expected = $manifest.artifacts.$stage2Name.sha256.ToLower()
        $actual   = Get-CortexSimFileSha256 -Path $stage2Path
        if ($expected -ne $actual) {
            throw "sha256 mismatch for $stage2Name`n  expected: $expected`n  actual:   $actual"
        }

        # Extract and invoke
        $stage2Dir = Join-Path $workdir "stage2"
        New-Item -ItemType Directory -Path $stage2Dir -Force | Out-Null
        Expand-Archive -Path $stage2Path -DestinationPath $stage2Dir -Force

        $entrypoint = Join-Path $stage2Dir "windows\install.ps1"
        if (-not (Test-Path $entrypoint)) {
            throw "stage-2 entrypoint missing: $entrypoint"
        }

        $args = @{
            Version = $Version
            DemoMode = [bool]($env:CORTEXSIM_DEMO_MODE -eq "1")
        }
        if ($Offline) { $args["OfflineBundleDir"] = $workdir }

        & $entrypoint @args
    }
    finally {
        Remove-Item -Recurse -Force $workdir -ErrorAction SilentlyContinue
    }
}

# If invoked directly (not dot-sourced), call with no args so `iex (iwr ...)` works.
if ($MyInvocation.InvocationName -ne '.') {
    # No auto-invocation when dot-sourced; user calls Install-CortexSim themselves.
}
```

- [ ] **Step 2: Run Pester tests and PSScriptAnalyzer**

```bash
pwsh -Command 'Invoke-Pester -Path tests/installer/pester/Bootstrap.Tests.ps1 -Output Detailed'
pwsh -Command 'Invoke-ScriptAnalyzer -Path installer/bootstrap/install.ps1 -Severity Warning'
```

Expected: all Pester tests pass, PSScriptAnalyzer clean.

- [ ] **Step 3: Commit**

```bash
git add installer/bootstrap/install.ps1
git commit -m "feat: stage-1 PowerShell bootstrap with SHA verification"
```

---

### Task 6: Stage-2 Windows — WSL2 bootstrap script

**Files:**
- Create: `installer/stage2/windows/wsl-bootstrap.ps1`

- [ ] **Step 1: Implement wsl-bootstrap.ps1**

Write to `installer/stage2/windows/wsl-bootstrap.ps1`:

```powershell
# wsl-bootstrap.ps1 — ensures WSL2 + Ubuntu-22.04 distro are installed and ready.
# Dot-sourced by install.ps1. Provides Enable-CortexSimWSL and Ensure-UbuntuDistro.

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\common\annotate.ps1")

function Test-WSLInstalled {
    $feat = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -ErrorAction SilentlyContinue
    if (-not $feat -or $feat.State -ne "Enabled") { return $false }
    $vmp = Get-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -ErrorAction SilentlyContinue
    if (-not $vmp -or $vmp.State -ne "Enabled") { return $false }
    try { wsl.exe --status | Out-Null } catch { return $false }
    return $true
}

function Enable-CortexSimWSL {
    if (Test-WSLInstalled) {
        Invoke-Annotate -Technique "-" -Action "wsl_already_enabled"
        return $false  # false = no reboot needed
    }

    Invoke-Annotate -Technique "T1059.001" -Action "enabling_wsl_feature"
    # Use the modern one-shot installer; requires Win10 2004+ / Server 2022
    # --no-distribution so we pick Ubuntu-22.04 explicitly in the next step.
    wsl.exe --install --no-distribution
    if ($LASTEXITCODE -ne 0) {
        # Fallback: enable features manually and require reboot.
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -All -NoRestart | Out-Null
        Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -All -NoRestart | Out-Null
        Invoke-Annotate -Technique "-" -Action "wsl_features_enabled_reboot_required"
        return $true  # reboot needed
    }
    Invoke-Annotate -Technique "-" -Action "wsl_installed_no_reboot"
    return $false
}

function Ensure-UbuntuDistro {
    param([string]$DistroName = "Ubuntu-22.04")

    $installed = wsl.exe --list --quiet 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ -eq $DistroName }
    if ($installed) {
        Invoke-Annotate -Technique "-" -Action "wsl_distro_already_installed" -Extra @{distro=$DistroName}
        return
    }

    Invoke-Annotate -Technique "T1059.001" -Action "installing_wsl_distro" -Extra @{distro=$DistroName}
    wsl.exe --install -d $DistroName --no-launch
    if ($LASTEXITCODE -ne 0) {
        throw "wsl --install -d $DistroName failed (exit $LASTEXITCODE)"
    }

    # First-run initialization with a non-interactive root-only setup.
    # We don't create an interactive user because the service runs as LocalSystem.
    wsl.exe -d $DistroName -u root -- bash -c "echo 'ubuntu-ready' && true"
    if ($LASTEXITCODE -ne 0) {
        throw "WSL distro $DistroName failed initial boot"
    }

    # Ensure WSL2 backend (not WSL1)
    wsl.exe --set-version $DistroName 2 2>$null | Out-Null
    wsl.exe --set-default $DistroName
}

function Copy-Stage2ToPersistent {
    # Copy the entire stage-2 directory (currently running from a temp location
    # that will be wiped on reboot) into $env:ProgramData\CortexSim\stage2\ so the
    # post-reboot resume task can find it. Returns the persistent root path.
    $persistRoot = "$env:ProgramData\CortexSim\stage2"
    if (Test-Path $persistRoot) { Remove-Item -Recurse -Force $persistRoot }
    New-Item -ItemType Directory -Path $persistRoot -Force | Out-Null
    # $PSScriptRoot at call time is .../stage2/windows; we want the parent (common + windows + linux)
    $stage2Src = Split-Path -Parent $PSScriptRoot
    Copy-Item -Path (Join-Path $stage2Src "*") -Destination $persistRoot -Recurse -Force
    return $persistRoot
}

function Register-PostRebootResume {
    param(
        [string]$PersistentStage2Root,
        [string]$Version,
        [bool]$DemoMode
    )
    # Create a scheduled task that re-invokes the installer after reboot.
    # Runs once at startup as SYSTEM. The task self-deletes after successful run
    # via a -RunOnce flag check inside the resumed install.ps1.
    Invoke-Annotate -Technique "T1053.005" -Action "registered_post_reboot_resume_task"

    $resumeScript = Join-Path $PersistentStage2Root "windows\install.ps1"
    $demoFlag = if ($DemoMode) { " -DemoMode" } else { "" }
    $cmd = "& '$resumeScript' -Version '$Version' -ResumedFromReboot" + $demoFlag

    $taskName = "CortexSim-Install-Resume"
    $action = New-ScheduledTaskAction -Execute "pwsh.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$cmd`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

function Unregister-PostRebootResume {
    # Called by the resumed install.ps1 on successful completion so the task doesn't re-fire.
    Unregister-ScheduledTask -TaskName "CortexSim-Install-Resume" -Confirm:$false -ErrorAction SilentlyContinue
}
```

- [ ] **Step 2: PSScriptAnalyzer check**

```bash
pwsh -Command 'Invoke-ScriptAnalyzer -Path installer/stage2/windows/wsl-bootstrap.ps1 -Severity Warning'
```

Expected: clean (PSUseApprovedVerbs may warn on `Ensure-UbuntuDistro` — acceptable; we can optionally rename to `Install-UbuntuDistro` to silence it, but `Ensure-` reads better. Suppress with `[Diagnostics.CodeAnalysis.SuppressMessageAttribute]` if desired or just accept the warning).

- [ ] **Step 3: Commit**

```bash
git add installer/stage2/windows/wsl-bootstrap.ps1
git commit -m "feat: WSL2 + Ubuntu-22.04 bootstrap for Windows stage-2"
```

---

### Task 7: Stage-2 Windows — docker-in-wsl.sh

**Files:**
- Create: `installer/stage2/windows/docker-in-wsl.sh`

- [ ] **Step 1: Implement docker-in-wsl.sh**

Write to `installer/stage2/windows/docker-in-wsl.sh`:

```bash
#!/usr/bin/env bash
# docker-in-wsl.sh — run INSIDE the Ubuntu WSL distro to install docker-ce
# and configure it for headless use (no Docker Desktop).
# Invoked by windows/install.ps1 as:
#   wsl -d Ubuntu-22.04 -u root -- bash /mnt/c/.../docker-in-wsl.sh
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg iproute2
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $codename stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# WSL has no systemd by default on older installs; enable it via wsl.conf if missing.
if [ ! -f /etc/wsl.conf ] || ! grep -q "^systemd=true" /etc/wsl.conf 2>/dev/null; then
    cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true
[automount]
enabled=true
EOF
    echo "systemd enabled in wsl.conf — distro must be restarted (wsl --shutdown)"
fi

# Ensure docker service starts when the WSL distro boots.
if command -v systemctl >/dev/null 2>&1; then
    systemctl enable docker 2>/dev/null || true
fi

# Smoke test (tolerant of systemd not being live yet on first run)
docker --version
```

- [ ] **Step 2: Shellcheck**

```bash
shellcheck installer/stage2/windows/docker-in-wsl.sh
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add installer/stage2/windows/docker-in-wsl.sh
git commit -m "feat: docker-ce install script that runs inside the Ubuntu WSL distro"
```

---

### Task 8: Stage-2 Windows — service supervisor

**Files:**
- Create: `installer/stage2/windows/supervisor.ps1`

- [ ] **Step 1: Implement supervisor.ps1**

Write to `installer/stage2/windows/supervisor.ps1`:

```powershell
# supervisor.ps1 — long-running process wrapped by the cortexsim Windows service.
# Runs `wsl.exe -d Ubuntu-22.04 -- docker compose up` in the foreground so the
# service lifetime matches the container lifetime. Forwards service-stop into
# `docker compose down`.

$ErrorActionPreference = "Stop"

$DistroName = "Ubuntu-22.04"
$ComposePath = "/opt/cortexsim/docker-compose.yml"
$LogDir = "$env:ProgramData\CortexSim\logs"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

$stdoutLog = Join-Path $LogDir "cortexsim.stdout.log"
$stderrLog = Join-Path $LogDir "cortexsim.stderr.log"

function Stop-Compose {
    wsl.exe -d $DistroName -u root -- docker compose -f $ComposePath down --remove-orphans 2>&1 | Out-File -Append $stderrLog
}

# Register stop handler so the service can shut the compose project down gracefully.
Register-EngineEvent PowerShell.Exiting -Action { Stop-Compose } | Out-Null

try {
    # Bring up foreground compose; stdout/stderr go to log files.
    & wsl.exe -d $DistroName -u root -- docker compose -f $ComposePath up `
        2>> $stderrLog `
        >> $stdoutLog
    $exit = $LASTEXITCODE
} catch {
    $_ | Out-File -Append $stderrLog
    $exit = 1
} finally {
    Stop-Compose
}

exit $exit
```

- [ ] **Step 2: PSScriptAnalyzer check**

```bash
pwsh -Command 'Invoke-ScriptAnalyzer -Path installer/stage2/windows/supervisor.ps1 -Severity Warning'
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add installer/stage2/windows/supervisor.ps1
git commit -m "feat: Windows service supervisor wrapping wsl docker compose up"
```

---

### Task 9: Stage-2 Windows — service registration helpers

**Files:**
- Create: `installer/stage2/windows/cortexsim-service.ps1`

- [ ] **Step 1: Implement cortexsim-service.ps1**

Write to `installer/stage2/windows/cortexsim-service.ps1`:

```powershell
# cortexsim-service.ps1 — create/remove the cortexsim Windows service.
# Service runs supervisor.ps1 under LocalSystem.

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "..\common\annotate.ps1")

$ServiceName = "cortexsim"
$SupervisorPath = "$env:ProgramData\CortexSim\supervisor.ps1"

function Install-CortexSimService {
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Write-Host "Service $ServiceName already exists; replacing."
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe delete $ServiceName | Out-Null
        Start-Sleep -Seconds 2
    }

    $binPath = "pwsh.exe -NoProfile -ExecutionPolicy Bypass -File `"$SupervisorPath`""
    # sc.exe requires exact spacing after '='
    sc.exe create $ServiceName binPath= $binPath start= auto DisplayName= "CortexSim" | Out-Null
    sc.exe description $ServiceName "CortexSim detection simulation engine (supervises WSL2 docker compose)" | Out-Null
    # Failure recovery: restart on crash
    sc.exe failure $ServiceName reset= 86400 actions= restart/10000/restart/10000/restart/10000 | Out-Null

    Invoke-Annotate -Technique "T1543.003" -Action "installed_windows_service" -Extra @{service=$ServiceName}
}

function Remove-CortexSimService {
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        sc.exe delete $ServiceName | Out-Null
    }
}

function Start-CortexSimService {
    Start-Service -Name $ServiceName
    Invoke-Annotate -Technique "T1569.002" -Action "started_service_managed_container"
}
```

- [ ] **Step 2: PSScriptAnalyzer check**

```bash
pwsh -Command 'Invoke-ScriptAnalyzer -Path installer/stage2/windows/cortexsim-service.ps1 -Severity Warning'
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add installer/stage2/windows/cortexsim-service.ps1
git commit -m "feat: Windows service register/remove/start helpers"
```

---

### Task 10: Stage-2 Windows — main install.ps1 entrypoint

**Files:**
- Create: `installer/stage2/windows/install.ps1`

- [ ] **Step 1: Implement the main entrypoint**

Write to `installer/stage2/windows/install.ps1`:

```powershell
<#
.SYNOPSIS
    CortexSim stage-2 installer (Windows).
.DESCRIPTION
    Called by stage-1 bootstrap. Enables WSL2, installs Ubuntu-22.04,
    installs docker-ce inside WSL, renders compose.yml, registers the
    cortexsim Windows service, starts it, and polls /api/health.
#>

param(
    [string]$Version = "latest",
    [switch]$DemoMode,
    [string]$OfflineBundleDir,
    [switch]$ResumedFromReboot
)

$ErrorActionPreference = "Stop"
$env:ANNOTATE_STAGE = "stage2-windows"
$env:CORTEXSIM_DEMO_MODE = if ($DemoMode) { "1" } else { "0" }
if (-not $env:CORTEXSIM_INSTALLER_RUN_ID) {
    $env:CORTEXSIM_INSTALLER_RUN_ID = [guid]::NewGuid().ToString()
}

. (Join-Path $PSScriptRoot "..\common\annotate.ps1")
. (Join-Path $PSScriptRoot "wsl-bootstrap.ps1")
. (Join-Path $PSScriptRoot "cortexsim-service.ps1")

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = New-Object Security.Principal.WindowsPrincipal($id)
    return $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Require-Elevation {
    if (-not (Test-Elevated)) {
        Invoke-Annotate -Technique "T1548.002" -Action "elevation_required_but_not_admin"
        throw "Stage-2 must run as Administrator. Re-run from an elevated PowerShell."
    }
    Invoke-Annotate -Technique "T1548.002" -Action "elevated"
}

function Test-WindowsBuildSupported {
    $build = [System.Environment]::OSVersion.Version.Build
    if ($build -lt 19041) {
        throw "Windows build $build not supported. WSL2 one-shot install requires build >= 19041 (Win10 2004+ / Win11 / Server 2022)."
    }
}

function Deploy-SupervisorAndCompose {
    param([string]$ImageRef)

    $dataRoot = "$env:ProgramData\CortexSim"
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    New-Item -ItemType Directory -Path "$dataRoot\data" -Force | Out-Null
    New-Item -ItemType Directory -Path "$dataRoot\logs" -Force | Out-Null

    # Copy supervisor to its permanent location (service points at this path)
    Copy-Item -Path (Join-Path $PSScriptRoot "supervisor.ps1") -Destination "$dataRoot\supervisor.ps1" -Force

    # Render compose.yml inside the WSL distro at /opt/cortexsim/
    $tmplPath = Join-Path $PSScriptRoot "..\common\compose.yml.tmpl"
    $tmpl = Get-Content -Path $tmplPath -Raw

    $rendered = $tmpl `
        -replace "__IMAGE__", $ImageRef `
        -replace "__DATA_DIR__", "/var/lib/cortexsim/data" `
        -replace "__PORT__", "8888"

    # Write to a tempfile on Windows side, then copy into WSL
    $tmpCompose = [System.IO.Path]::GetTempFileName()
    Set-Content -Path $tmpCompose -Value $rendered -Encoding UTF8
    $tmpComposeWslPath = (wsl.exe wslpath -a $tmpCompose).Trim()
    wsl.exe -d Ubuntu-22.04 -u root -- bash -c "mkdir -p /opt/cortexsim /var/lib/cortexsim/data && cp '$tmpComposeWslPath' /opt/cortexsim/docker-compose.yml"
    Remove-Item -Force $tmpCompose
    Invoke-Annotate -Technique "-" -Action "rendered_compose_file"
}

function Load-Image-In-Wsl {
    param([string]$Version, [string]$OfflineBundleDir)

    if ($OfflineBundleDir) {
        $imgTar = Get-ChildItem -Path $OfflineBundleDir -Filter "cortexsim-linux-*.tar" | Select-Object -First 1
        if ($imgTar) {
            $wslPath = (wsl.exe wslpath -a $imgTar.FullName).Trim()
            Invoke-Annotate -Technique "-" -Action "loading_image_from_local" -Extra @{path=$imgTar.FullName}
            wsl.exe -d Ubuntu-22.04 -u root -- bash -c "docker load -i '$wslPath'"
            $script:ImageRef = (wsl.exe -d Ubuntu-22.04 -u root -- bash -c "docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(ghcr.io/.+/)?cortexsim:' | head -1").Trim()
            return
        }
    }

    $script:ImageRef = "ghcr.io/hankthebldr/cortexsim:$Version"
    Invoke-Annotate -Technique "T1105" -Action "pulling_image_from_ghcr" -Extra @{ref=$script:ImageRef}
    wsl.exe -d Ubuntu-22.04 -u root -- docker pull $script:ImageRef
}

function Install-DockerInsideWsl {
    $scriptSource = Join-Path $PSScriptRoot "docker-in-wsl.sh"
    $wslScriptPath = (wsl.exe wslpath -a $scriptSource).Trim()
    wsl.exe -d Ubuntu-22.04 -u root -- bash "$wslScriptPath"
    if ($LASTEXITCODE -ne 0) { throw "docker-in-wsl.sh failed (exit $LASTEXITCODE)" }
    # Restart distro to pick up systemd-enabled wsl.conf on first run.
    wsl.exe --shutdown
    Start-Sleep -Seconds 3
    wsl.exe -d Ubuntu-22.04 -u root -- bash -c "service docker start 2>/dev/null || systemctl start docker 2>/dev/null || true"
    Invoke-Annotate -Technique "-" -Action "installed_docker_ce_in_wsl"
}

function Wait-Healthy {
    $deadline = (Get-Date).AddSeconds(180)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8888/api/health" -TimeoutSec 3
            if ($resp.StatusCode -eq 200) {
                Invoke-Annotate -Technique "-" -Action "verified_local_http_endpoint"
                Write-Host "CortexSim is healthy: http://$([Environment]::MachineName):8888"
                return
            }
        } catch {
            Start-Sleep -Seconds 5
        }
    }
    throw "/api/health did not respond within 180s"
}

# --- Main flow ---
Invoke-Annotate -Technique "T1059.001" -Action "stage2_entered" -Extra @{version=$Version}
Require-Elevation
Test-WindowsBuildSupported

$rebootNeeded = Enable-CortexSimWSL
if ($rebootNeeded) {
    # Temp install dir will be wiped on reboot; copy stage-2 to a persistent location first.
    $persistRoot = Copy-Stage2ToPersistent
    Register-PostRebootResume -PersistentStage2Root $persistRoot -Version $Version -DemoMode:$DemoMode
    Write-Warning "WSL2 features enabled. Reboot required. Installer will resume automatically at next boot."
    Restart-Computer -Force
    return
}

if ($ResumedFromReboot) {
    Invoke-Annotate -Technique "-" -Action "installer_resumed_from_reboot"
}

Ensure-UbuntuDistro -DistroName "Ubuntu-22.04"
Install-DockerInsideWsl
Load-Image-In-Wsl -Version $Version -OfflineBundleDir $OfflineBundleDir
Deploy-SupervisorAndCompose -ImageRef $script:ImageRef
Install-CortexSimService
Start-CortexSimService
Wait-Healthy

# Post-install: clean up the post-reboot resume task if it was registered.
Unregister-PostRebootResume

Write-Host "==============================================="
Write-Host "CortexSim installed. http://127.0.0.1:8888"
Write-Host "Admin bootstrap token: see \$env:ProgramData\CortexSim\data\admin.token"
Write-Host "==============================================="
```

- [ ] **Step 2: PSScriptAnalyzer check**

```bash
pwsh -Command 'Invoke-ScriptAnalyzer -Path installer/stage2/windows/install.ps1 -Severity Warning'
```

Expected: clean (some PSUseApprovedVerbs warnings are OK and can be suppressed if desired).

- [ ] **Step 3: Commit**

```bash
git add installer/stage2/windows/install.ps1
git commit -m "feat: Windows stage-2 main entrypoint orchestrating WSL2+docker+service"
```

---

### Task 11: Windows uninstall script

**Files:**
- Create: `installer/stage2/windows/uninstall.ps1`

- [ ] **Step 1: Implement uninstall.ps1**

Write to `installer/stage2/windows/uninstall.ps1`:

```powershell
<#
.SYNOPSIS
    Remove CortexSim from a Windows host. Idempotent.
.PARAMETER KeepData
    If set, preserves %ProgramData%\CortexSim\data.
.PARAMETER RemoveWSLDistro
    If set, also unregisters the Ubuntu-22.04 WSL distro.
#>
param(
    [switch]$KeepData,
    [switch]$RemoveWSLDistro
)

$ErrorActionPreference = "Continue"
$ServiceName = "cortexsim"
$DataRoot = "$env:ProgramData\CortexSim"

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Stopping $ServiceName service..."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    sc.exe delete $ServiceName | Out-Null
}

Write-Host "Bringing down docker compose project in WSL..."
wsl.exe -d Ubuntu-22.04 -u root -- bash -c "test -f /opt/cortexsim/docker-compose.yml && docker compose -f /opt/cortexsim/docker-compose.yml down --remove-orphans || true" 2>$null
wsl.exe -d Ubuntu-22.04 -u root -- rm -rf /opt/cortexsim 2>$null

if ($KeepData) {
    Write-Host "Keeping $DataRoot\data\ (per -KeepData)"
    Get-ChildItem -Path $DataRoot -Exclude "data" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
} else {
    if (Test-Path $DataRoot) {
        Write-Host "Removing $DataRoot ..."
        Remove-Item -Recurse -Force $DataRoot -ErrorAction SilentlyContinue
    }
}

if ($RemoveWSLDistro) {
    Write-Host "Unregistering Ubuntu-22.04 WSL distro..."
    wsl.exe --unregister Ubuntu-22.04 2>$null
}

Write-Host "Uninstall complete."
```

- [ ] **Step 2: PSScriptAnalyzer**

```bash
pwsh -Command 'Invoke-ScriptAnalyzer -Path installer/stage2/windows/uninstall.ps1 -Severity Warning'
```

Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add installer/stage2/windows/uninstall.ps1
git commit -m "feat: Windows uninstall script"
```

---

### Task 12: Update build-stage2.sh to produce both Linux and Windows stage-2 archives

**Files:**
- Modify: `installer/scripts/build-stage2.sh`

- [ ] **Step 1: Extend build-stage2.sh**

Replace the contents of `installer/scripts/build-stage2.sh` with:

```bash
#!/usr/bin/env bash
# build-stage2.sh — produce stage2-linux.tar.gz and stage2-windows.zip.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT/installer/dist"
mkdir -p "$OUT_DIR"
STAGE2_ROOT="$ROOT/installer/stage2"

# ---- Linux ----
TMP_L="$(mktemp -d)"
trap 'rm -rf "$TMP_L" "$TMP_W"' EXIT
cp -r "$STAGE2_ROOT/common" "$TMP_L/"
cp -r "$STAGE2_ROOT/linux"  "$TMP_L/"
chmod +x "$TMP_L/linux/install" "$TMP_L/linux/"*.sh "$TMP_L/common/"*.sh 2>/dev/null || true
tar -czf "$OUT_DIR/stage2-linux.tar.gz" -C "$TMP_L" common linux

# ---- Windows ----
TMP_W="$(mktemp -d)"
cp -r "$STAGE2_ROOT/common" "$TMP_W/"
cp -r "$STAGE2_ROOT/windows" "$TMP_W/"
# zip up; requires `zip` on the host runner (present on GH ubuntu runners)
( cd "$TMP_W" && zip -r "$OUT_DIR/stage2-windows.zip" common windows >/dev/null )

echo "built:"
echo "  $OUT_DIR/stage2-linux.tar.gz"
echo "  $OUT_DIR/stage2-windows.zip"
```

- [ ] **Step 2: Run locally and verify**

```bash
installer/scripts/build-stage2.sh
installer/scripts/gen-manifest.sh v0.0.0-dev
cat installer/dist/manifest.json
```

Expected: manifest.json now lists both `stage2-linux.tar.gz` and `stage2-windows.zip`.

- [ ] **Step 3: Commit**

```bash
git add installer/scripts/build-stage2.sh
git commit -m "build: produce stage2-windows.zip alongside Linux tarball"
```

---

### Task 13: Windows integration test in CI

**Files:**
- Modify: `.github/workflows/installer-integration.yml`

- [ ] **Step 1: Add a Windows Server 2022 job**

Append to `.github/workflows/installer-integration.yml` as a sibling of `ubuntu-online`:

```yaml
  windows-online:
    runs-on: windows-2022
    permissions:
      contents: read
      packages: read
    steps:
      - uses: actions/checkout@v4

      - name: Ensure Docker is enabled (needed for pre-built image load)
        shell: pwsh
        run: |
          Write-Host "Docker version on runner:"
          docker version

      - name: Build SimCore image locally
        shell: pwsh
        run: |
          docker build -f core/Dockerfile -t ghcr.io/${{ github.repository_owner }}/cortexsim:ci-latest .
          docker save -o cortexsim-linux-amd64.tar ghcr.io/${{ github.repository_owner }}/cortexsim:ci-latest
          New-Item -ItemType Directory -Path installer/dist -Force | Out-Null
          Move-Item cortexsim-linux-amd64.tar installer/dist/cortexsim-linux-amd64.tar

      - name: Build stage-2 archives
        shell: bash
        run: |
          installer/scripts/build-stage2.sh
          installer/scripts/gen-manifest.sh ci-latest

      - name: Install via stage-1 with --local-stage2
        shell: pwsh
        run: |
          . .\installer\bootstrap\install.ps1
          Install-CortexSim -LocalStage2 "$PWD\installer\dist" -Version "ci-latest"

      - name: Poll health endpoint
        shell: pwsh
        run: |
          $deadline = (Get-Date).AddSeconds(300)
          while ((Get-Date) -lt $deadline) {
            try {
              $r = Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:8888/api/health -TimeoutSec 5
              if ($r.StatusCode -eq 200) {
                Write-Host "healthy"
                exit 0
              }
            } catch {
              Start-Sleep -Seconds 10
            }
          }
          Write-Error "/api/health did not respond"
          Get-EventLog -LogName Application -Source "CortexSim-Installer" -Newest 50 | Format-List
          Get-Content "$env:ProgramData\CortexSim\logs\cortexsim.stderr.log" -Tail 100
          exit 1

      - name: Uninstall
        shell: pwsh
        run: |
          . .\installer\stage2\windows\uninstall.ps1
```

- [ ] **Step 2: Verify YAML parses**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/installer-integration.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/installer-integration.yml
git commit -m "ci: Windows Server 2022 installer integration test"
```

---

### Task 14: CI lint job — add Pester + PSScriptAnalyzer

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a PowerShell lint job**

Add this job to `.github/workflows/ci.yml` as a sibling of `lint-shell`:

```yaml
  lint-powershell:
    runs-on: windows-2022
    steps:
      - uses: actions/checkout@v4

      - name: Install Pester + PSScriptAnalyzer
        shell: pwsh
        run: |
          Install-Module -Name Pester -Force -SkipPublisherCheck -Scope CurrentUser
          Install-Module -Name PSScriptAnalyzer -Force -Scope CurrentUser

      - name: Run PSScriptAnalyzer on installer PS scripts
        shell: pwsh
        run: |
          $issues = Invoke-ScriptAnalyzer -Path installer/ -Recurse -Severity Warning
          if ($issues) {
            $issues | Format-Table -AutoSize
            exit 1
          }

      - name: Run Pester tests
        shell: pwsh
        run: |
          $res = Invoke-Pester -Path tests/installer/pester/ -PassThru
          if ($res.FailedCount -gt 0) { exit 1 }
```

- [ ] **Step 2: YAML parse check**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: PSScriptAnalyzer + Pester linting for Windows installer scripts"
```

---

### Task 15: README — Windows quick-install section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append Windows section beneath the Linux quick-install**

Find the `## Quick Install (Linux)` section added in Plan A. Directly below it, add:

```markdown
## Quick Install (Windows)

> Requires Windows Server 2022 / Windows 10 2004+ / Windows 11 with Administrator access.
> Installer enables WSL2, installs Ubuntu-22.04, and installs docker-ce inside it (no Docker Desktop license needed).
> A single reboot may be required on hosts where WSL2 is not yet enabled; the installer registers a one-shot scheduled task to resume automatically.

Open an **elevated PowerShell** and run:

    iex (iwr -useb https://github.com/hankthebldr/cortexsim/releases/latest/download/install.ps1); Install-CortexSim

Add `-DemoMode` to emit ATT&CK-tagged telemetry at each install stage. Offline:

    Install-CortexSim -Offline .\cortexsim-windows-amd64.zip
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: Windows quick-install section"
```

---

**End of Plan B.** After this plan: Windows Server 2022 / Win10 2004+ / Win11 hosts can run the one-line PowerShell cradle, have WSL2 + Ubuntu-22.04 + docker-ce automatically installed, and end up with a healthy CortexSim container reachable on `http://127.0.0.1:8888`. CI lints both shell and PowerShell, and runs end-to-end integration tests on both Ubuntu and Windows Server runners. Plan C adds the release-on-tag workflow, offline bundles, and demo-mode self-validation.
