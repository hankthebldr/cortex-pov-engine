<#
.SYNOPSIS
    Drives the `windows` CI job (.github/workflows/ci.yml), one phase per call.

.DESCRIPTION
    Runs ONLY on a GitHub-hosted windows-2022 runner under Windows PowerShell
    5.1 (the product floor). Every phase is a separate workflow step so a
    failure names the gate that failed instead of masking the ones after it.
    State that later phases need (SimCore pid, agent id, draft ids) is kept in
    <RUNNER_TEMP>\windows-proof\state.json; everything a reviewer needs to
    audit a run is written next to it and uploaded as the `windows-proof`
    artifact.

      floor      Windows PowerShell 5.1 Desktop, or throw. Records the host.
      go         native `go vet` + `go test ./... -count=1 -json` with Git's sh
                 stripped from PATH; any TestPowerShell* skip is a failure.
      build      the beacon matrix the shipped recipe (scripts/build-agent-dist.sh)
                 produced: PE32+ x86-64 console exe, SHA256SUMS, Authenticode.
      render     scripts/ci/render_powershell.py --out <RUNNER_TEMP>\ps.
      parse      every rendered file through Parser::ParseFile (BOM, else ANSI,
                 as -File reads it) AND Parser::ParseInput on its UTF-8 text
                 (as iwr | iex reads it). Then three control fixtures with a
                 known verdict, two of which this parser MUST reject, so the
                 phase is not a gate that can only ever say yes.
      pssa       PSScriptAnalyzer: PSUseCompatibleSyntax 5.1 plus
                 PSUseCompatibleCommands / PSUseCompatibleTypes against the
                 Server 2016 / 2019 Windows PowerShell 5.1 profiles.
      boot       SimCore under uvicorn on 127.0.0.1:8888 from this checkout.
      install    the console's token one-liner (iwr | iex) in a child
                 powershell.exe; service Running, no SCM failures, beaconing.
      pull       probe drafts over the pull path: exit codes, fail-fast,
                 step_outcomes, stderr as plain text, marker on disk.
      abort      POST /abort kills the step tree and nothing outside it.
      push       the PowerShell push bundle for the same probe, run with -File.
      uninstall  the uninstall one-liner leaves no service, dir or process.
      collect    SCM events, sc.exe, Defender state, SimCore db; always runs.

    What this proves is the behaviour of THIS runner image. It does not prove
    a customer host (see the job header in ci.yml for the list), and it never
    touches a Cortex tenant: tenant-verified stays 0.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('floor', 'go', 'build', 'render', 'parse', 'pssa', 'boot', 'install', 'pull', 'abort', 'push', 'uninstall', 'collect')]
    [string]$Phase,

    [string]$Server = 'http://127.0.0.1:8888'
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

if ($env:GITHUB_WORKSPACE) {
    $script:Workspace = $env:GITHUB_WORKSPACE
} else {
    $script:Workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
}
if ($env:RUNNER_TEMP) {
    $script:RunnerTemp = $env:RUNNER_TEMP
} else {
    $script:RunnerTemp = [System.IO.Path]::GetTempPath()
}
$script:ProofDir    = Join-Path $script:RunnerTemp 'windows-proof'
$script:StateFile   = Join-Path $script:ProofDir 'state.json'
$script:AgentDist   = Join-Path $script:RunnerTemp 'agent-dist'
$script:RenderDir   = Join-Path $script:RunnerTemp 'ps'
$script:InstallDir  = Join-Path $env:ProgramData 'CortexSim'
$script:MarkerFile  = Join-Path $script:InstallDir 'ci-marker.txt'
$script:ServiceName = 'CortexSimAgent'
$script:WinPs       = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$script:WinExe      = 'cortexsim-agent-windows-amd64.exe'
$script:Utf8NoBom   = New-Object System.Text.UTF8Encoding($false)
$script:Failures    = New-Object System.Collections.Generic.List[string]

# The full matrix scripts/build-agent-dist.sh ships. Kept in step with that
# script and core/Dockerfile by tests/installer/test_agent_dist_matrix_parity.py.
$script:DistTargets = @(
    'cortexsim-agent-linux-amd64',
    'cortexsim-agent-linux-arm64',
    'cortexsim-agent-darwin-amd64',
    'cortexsim-agent-darwin-arm64',
    'cortexsim-agent-windows-amd64.exe'
)

# Components that must read ok on a fresh CI boot. payload_shelf (nothing
# staged), integrations (no credential), eal_collectors (none configured),
# agents and task_queue legitimately read otherwise here; they are recorded,
# not gated.
$script:HealthRequired = @(
    'db', 'scenario_catalog', 'ttp_catalog', 'assertion_catalog',
    'adapter_catalog', 'uctc_registry', 'identity_spec', 'agent_binaries'
)

New-Item -ItemType Directory -Force -Path $script:ProofDir | Out-Null

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

function Write-CsInfo {
    param([string]$Message)
    Write-Host ('[windows-native-e2e/' + $Phase + '] ' + $Message)
}

function Limit-Cs {
    param([string]$Text, [int]$Max = 600)
    if ($null -eq $Text) { return '' }
    if ($Text.Length -le $Max) { return $Text }
    return $Text.Substring(0, $Max) + '...'
}

function Assert-Cs {
    param([bool]$Condition, [string]$What, [string]$Detail = '')
    if ($Condition) {
        Write-Host ('[ok]   ' + $What)
        return
    }
    $line = $What
    if ($Detail) { $line = $line + ' -- ' + $Detail }
    Write-Host ('[FAIL] ' + $line)
    Write-Host ('::error title=windows ' + $Phase + '::' + (($line -replace '[\r\n]+', ' ') -replace '::', ': :'))
    $script:Failures.Add($line)
}

function Add-CsSummary {
    param([string]$Line)
    if ($env:GITHUB_STEP_SUMMARY) {
        [System.IO.File]::AppendAllText($env:GITHUB_STEP_SUMMARY, $Line + "`n", $script:Utf8NoBom)
    }
}

function Write-CsFile {
    param([string]$Path, [string]$Text)
    if ($null -eq $Text) { $Text = '' }
    [System.IO.File]::WriteAllText($Path, $Text, $script:Utf8NoBom)
}

function Write-CsProof {
    param([string]$Name, [string]$Text)
    Write-CsFile -Path (Join-Path $script:ProofDir $Name) -Text $Text
}

# One flat JSON object per record. ConvertTo-Json on 5.1 can serialise an
# array that went through the pipeline as {"value":[...],"Count":n}, so
# anything uploaded as a record is built from scalar-only objects and joined
# here rather than handed to ConvertTo-Json as an array.
function ConvertTo-CsJsonArray {
    param([object[]]$Items)
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($Items)) {
        if ($null -eq $item) { continue }
        $parts.Add((ConvertTo-Json -InputObject $item -Compress -Depth 3))
    }
    return '[' + ($parts -join ",`n") + ']'
}

# ---------------------------------------------------------------------------
# State carried between phases (scalars only)
# ---------------------------------------------------------------------------

function Get-CsState {
    if (Test-Path -LiteralPath $script:StateFile) {
        $raw = [System.IO.File]::ReadAllText($script:StateFile)
        if ($raw.Trim()) { return (ConvertFrom-Json -InputObject $raw) }
    }
    return (New-Object PSObject)
}

function Get-CsStateValue {
    param([string]$Name)
    $prop = (Get-CsState).PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

function Set-CsStateValue {
    param([string]$Name, $Value)
    $state = Get-CsState
    $state | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
    Write-CsFile -Path $script:StateFile -Text (ConvertTo-Json -InputObject $state -Depth 3)
}

function Get-CsProp {
    param($Obj, [string]$Name)
    if ($null -eq $Obj) { return $null }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) { return $Obj[$Name] }
        return $null
    }
    $prop = $Obj.PSObject.Properties[$Name]
    if ($null -ne $prop) { return $prop.Value }
    return $null
}

function ConvertTo-CsUtc {
    param([string]$Iso)
    if (-not $Iso) { return $null }
    $styles = [System.Globalization.DateTimeStyles]::AdjustToUniversal -bor [System.Globalization.DateTimeStyles]::AssumeUniversal
    return [datetime]::Parse($Iso, [System.Globalization.CultureInfo]::InvariantCulture, $styles)
}

# ---------------------------------------------------------------------------
# HTTP against SimCore
# ---------------------------------------------------------------------------

function Invoke-Cs {
    param(
        [string]$Method = 'GET',
        [Parameter(Mandatory = $true)][string]$Path,
        [byte[]]$BodyBytes = $null,
        [int]$TimeoutSec = 30,
        [string]$OutFile = ''
    )
    $params = @{
        Uri             = $Server.TrimEnd('/') + $Path
        Method          = $Method
        UseBasicParsing = $true
        TimeoutSec      = $TimeoutSec
    }
    if ($null -ne $BodyBytes) {
        # Bytes, never a string: 5.1 encodes a string body as ISO-8859-1.
        $params['Body'] = $BodyBytes
        $params['ContentType'] = 'application/json; charset=utf-8'
    }
    if ($OutFile) {
        $params['OutFile'] = $OutFile
        $params['PassThru'] = $true
    }
    $result = New-Object PSObject -Property @{ Status = 0; Text = ''; Json = $null; Headers = $null; Error = '' }
    try {
        $resp = Invoke-WebRequest @params
        $result.Status = [int]$resp.StatusCode
        $result.Headers = $resp.Headers
        if (-not $OutFile) {
            # FastAPI sends application/json with no charset; 5.1 would decode
            # .Content as ISO-8859-1. Decode the raw bytes as UTF-8 instead.
            $result.Text = [System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
        }
    } catch {
        $ex = $_.Exception
        $errResp = $null
        if ($null -ne $ex.PSObject.Properties['Response']) { $errResp = $ex.Response }
        if ($null -ne $errResp) {
            $result.Status = [int]$errResp.StatusCode
            if ($null -ne $_.ErrorDetails -and $_.ErrorDetails.Message) { $result.Text = $_.ErrorDetails.Message }
        } else {
            $result.Error = $ex.Message
        }
    }
    if ($result.Text) {
        try { $result.Json = ConvertFrom-Json -InputObject $result.Text } catch { $result.Json = $null }
    }
    return $result
}

function Invoke-CsJson {
    param(
        [string]$Method = 'GET',
        [Parameter(Mandatory = $true)][string]$Path,
        [byte[]]$BodyBytes = $null,
        [int[]]$Expect = @(200),
        [string]$SaveAs = ''
    )
    $r = Invoke-Cs -Method $Method -Path $Path -BodyBytes $BodyBytes
    if ($SaveAs) { Write-CsProof -Name $SaveAs -Text $r.Text }
    if ($Expect -notcontains $r.Status) {
        throw ($Method + ' ' + $Path + ' returned HTTP ' + $r.Status + ' (expected ' + ($Expect -join '/') + ') ' + $r.Error + ' ' + (Limit-Cs $r.Text 800))
    }
    # An unrecognised body must raise, never read as empty.
    if ($null -eq $r.Json) {
        throw ($Method + ' ' + $Path + ' returned HTTP ' + $r.Status + ' with a body that is not JSON: ' + (Limit-Cs $r.Text 400))
    }
    return $r.Json
}

function ConvertTo-CsBody {
    param([hashtable]$Table)
    return [System.Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Table -Compress))
}

function Get-CsHeader {
    param($Headers, [string]$Name)
    if ($null -eq $Headers) { return '' }
    foreach ($key in @($Headers.Keys)) {
        if ($key -eq $Name) { return ((@($Headers[$key]) | ForEach-Object { [string]$_ }) -join ',') }
    }
    return ''
}

# ---------------------------------------------------------------------------
# Native processes
# ---------------------------------------------------------------------------

# Run a native command line through cmd.exe so redirection happens in cmd:
# 5.1's `>` writes UTF-16, and native stderr under EAP=Stop throws.
function Invoke-CsCmd {
    param([string]$CommandLine)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & cmd.exe /d /c $CommandLine | Out-Host
        return [int]$LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Start-CsChild {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$Name,
        [int]$TimeoutSec = 300,
        [string]$WorkingDirectory = ''
    )
    if (-not $WorkingDirectory) { $WorkingDirectory = $script:Workspace }
    $out = Join-Path $script:ProofDir ($Name + '.stdout.txt')
    $err = Join-Path $script:ProofDir ($Name + '.stderr.txt')
    $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -PassThru -NoNewWindow `
        -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $out -RedirectStandardError $err
    # 5.1 hands back Process.GetProcessById(pid): unless Handle is opened while
    # the process is alive, ExitCode reads $null after it exits. A null exit
    # code is never read as success by any caller.
    try { $null = $proc.Handle } catch { Write-CsInfo ($Name + ': could not open a process handle: ' + $_.Exception.Message) }
    $finished = $proc.WaitForExit($TimeoutSec * 1000)
    if (-not $finished) {
        Write-CsInfo ($Name + ' exceeded ' + $TimeoutSec + ' s; killing its process tree')
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { & taskkill.exe /T /F /PID $proc.Id 2>&1 | Out-Host } finally { $ErrorActionPreference = $prev }
        $null = $proc.WaitForExit(10000)
    }
    $code = $null
    if ($proc.HasExited) { $code = $proc.ExitCode }
    $stdout = ''
    $stderr = ''
    if (Test-Path -LiteralPath $out) { $stdout = [System.IO.File]::ReadAllText($out) }
    if (Test-Path -LiteralPath $err) { $stderr = [System.IO.File]::ReadAllText($err) }
    Write-Host ('----- ' + $Name + ' stdout -----')
    Write-Host $stdout
    if ($stderr.Trim()) {
        Write-Host ('----- ' + $Name + ' stderr -----')
        Write-Host $stderr
    }
    Write-Host ('----- ' + $Name + ' exit=' + $code + ' timed_out=' + (-not $finished) + ' -----')
    return New-Object PSObject -Property @{ ExitCode = $code; TimedOut = (-not $finished); Stdout = $stdout; Stderr = $stderr }
}

function Test-CsElevated {
    $id = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($id)
    return $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---------------------------------------------------------------------------
# Process trees
# ---------------------------------------------------------------------------

function Get-CsProcessTable {
    return @(Get-CimInstance -ClassName Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CreationDate, CommandLine)
}

# Every descendant of $RootPid. A child must have been created after its
# parent: Windows reuses PIDs, and without that check an unrelated process
# whose ParentProcessId happens to match a recycled PID reads as a descendant.
function Get-CsDescendants {
    param([int]$RootPid)
    $table = Get-CsProcessTable
    $byPid = @{}
    $byParent = @{}
    foreach ($p in $table) {
        $byPid[[int]$p.ProcessId] = $p
        $parent = [int]$p.ParentProcessId
        if (-not $byParent.ContainsKey($parent)) { $byParent[$parent] = New-Object System.Collections.ArrayList }
        [void]$byParent[$parent].Add($p)
    }
    $out = New-Object System.Collections.ArrayList
    $seen = @{}
    $queue = New-Object System.Collections.Queue
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $cur = [int]$queue.Dequeue()
        if ($seen.ContainsKey($cur)) { continue }
        $seen[$cur] = $true
        if (-not $byParent.ContainsKey($cur)) { continue }
        $parentInfo = $null
        if ($byPid.ContainsKey($cur)) { $parentInfo = $byPid[$cur] }
        foreach ($child in $byParent[$cur]) {
            if ([int]$child.ProcessId -eq $cur) { continue }
            if ($null -ne $parentInfo -and $null -ne $parentInfo.CreationDate -and $null -ne $child.CreationDate) {
                if ($child.CreationDate -lt $parentInfo.CreationDate) { continue }
            }
            [void]$out.Add($child)
            $queue.Enqueue([int]$child.ProcessId)
        }
    }
    # Unrolled on purpose: callers wrap in @() and pipe it into Where-Object.
    return $out.ToArray()
}

# True when the process recorded in $Info is still the same live process
# (same PID AND same creation time, so a recycled PID does not count).
function Test-CsSameProcess {
    param($Info)
    if ($null -eq $Info) { return $false }
    $now = @(Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + [int]$Info.ProcessId))
    if ($now.Count -eq 0) { return $false }
    return ($now[0].CreationDate -eq $Info.CreationDate)
}

function Format-CsProcs {
    param([object[]]$Procs)
    return ((@($Procs) | ForEach-Object { [string]$_.Name + '#' + [string]$_.ProcessId }) -join ', ')
}

function Get-CsBeaconRootPid {
    $svc = @(Get-CimInstance -ClassName Win32_Service -Filter ("Name='" + $script:ServiceName + "'"))
    if ($svc.Count -gt 0 -and [int]$svc[0].ProcessId -gt 0) { return [int]$svc[0].ProcessId }
    $proc = @(Get-Process -Name 'cortexsim-agent' -ErrorAction SilentlyContinue)
    if ($proc.Count -gt 0) { return [int]$proc[0].Id }
    return 0
}

# ---------------------------------------------------------------------------
# SimCore objects
# ---------------------------------------------------------------------------

# The three CI drafts. Each is POSTed as the exact bytes on disk, never
# round-tripped through 5.1's ConvertFrom/ConvertTo-Json.
function Get-CsFixtureDraftPath {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('probe', 'cmdlet_error', 'abort')]
        [string]$Role
    )
    $name = switch ($Role) {
        'probe'        { 'windows_ci_probe_draft.json' }
        'cmdlet_error' { 'windows_ci_cmdlet_error_draft.json' }
        'abort'        { 'windows_ci_abort_draft.json' }
    }
    return (Join-Path $script:Workspace ('tests\fixtures\' + $name))
}

function New-CsDraft {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Key)
    $cached = Get-CsStateValue ('draft_' + $Key)
    if ($cached) { return [string]$cached }
    $doc = Invoke-CsJson -Method POST -Path '/api/scenarios/drafts' -BodyBytes ([System.IO.File]::ReadAllBytes($Path)) `
        -Expect @(201) -SaveAs ('draft-' + $Key + '.json')
    $verdict = $doc.launchable
    if (-not $verdict.launchable) {
        throw ('draft ' + $Key + ' saved but is not launchable: ' + ((@($verdict.reasons) | ForEach-Object { [string]$_ }) -join '; '))
    }
    Set-CsStateValue ('draft_' + $Key) ([string]$doc.scenario_id)
    Write-CsInfo ('draft ' + $Key + ' = ' + $doc.scenario_id)
    return [string]$doc.scenario_id
}

function Start-CsPullRun {
    param([string]$ScenarioId, [string]$AgentId, [string]$Key)
    $body = ConvertTo-CsBody @{ scenario_id = $ScenarioId; mode = 'pull'; target_agent_id = $AgentId }
    $resp = Invoke-CsJson -Method POST -Path '/api/runs' -BodyBytes $body -SaveAs ('launch-' + $Key + '.json')
    Write-CsInfo ('run ' + $Key + ' = ' + $resp.run_id)
    return [string]$resp.run_id
}

function Wait-CsRun {
    param([string]$RunId, [int]$TimeoutSec, [string]$SaveAs, [string[]]$Terminal = @('complete', 'failed', 'aborted'))
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = $null
    do {
        $r = Invoke-Cs -Path ('/api/runs/' + $RunId)
        if ($r.Status -eq 200 -and $null -ne $r.Json) {
            $last = $r
            if ($Terminal -contains [string]$r.Json.status) { break }
        }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($null -ne $last) {
        Write-CsProof -Name $SaveAs -Text $last.Text
        return $last.Json
    }
    return $null
}

function Get-CsOutcomeMap {
    param($Run)
    $map = @{}
    foreach ($row in @($Run.step_outcomes)) { $map[[string]$row.step_id] = $row }
    return $map
}

# Split the run's aggregated output into per-step blocks keyed by step id.
# The beacon opens each block with "=== STEP n/N <sep> <step-id> <sep> ..." and
# closes it with "--- exit_code=N ... ---" and NO newline, so every header
# after the first sits mid-line: the split must not anchor on line start.
function Get-CsStepBlocks {
    param([string]$Output)
    $blocks = @{}
    if (-not $Output) { return $blocks }
    foreach ($part in [regex]::Split($Output, '=== STEP ')) {
        $m = [regex]::Match($part, '^\d+/\d+\s+\S+\s+(?<id>\S+)')
        if (-not $m.Success) { continue }
        $id = $m.Groups['id'].Value
        if ($blocks.ContainsKey($id)) { $blocks[$id] = $blocks[$id] + "`n" + $part } else { $blocks[$id] = $part }
    }
    return $blocks
}

function Assert-CsOutcome {
    param($Map, [string]$StepId, [string]$Termination, $Rc = $null, [string]$Reason = '')
    if (-not $Map.ContainsKey($StepId)) {
        Assert-Cs $false ($StepId + ' has a step_outcomes row') ('rows: ' + (@($Map.Keys) -join ','))
        return
    }
    $row = $Map[$StepId]
    $rc = Get-CsProp $row 'rc'
    Assert-Cs ([string]$row.termination -eq $Termination) ($StepId + ' termination=' + $Termination) ('got ' + $row.termination + ' rc=' + $rc)
    if ($Rc -is [string] -and $Rc -eq 'nonzero') {
        Assert-Cs (($null -ne $rc) -and ([int]$rc -ne 0)) ($StepId + ' rc != 0') ('got rc=' + $rc)
    } elseif ($null -ne $Rc) {
        Assert-Cs (($null -ne $rc) -and ([int]$rc -eq [int]$Rc)) ($StepId + ' rc=' + $Rc) ('got rc=' + $rc)
    }
    if ($Reason) {
        Assert-Cs ([string](Get-CsProp $row 'reason') -eq $Reason) ($StepId + ' reason=' + $Reason) ('got ' + (Get-CsProp $row 'reason'))
    }
}

function Get-CsAgentRow {
    param([string]$AgentId)
    $list = Invoke-CsJson -Path '/api/agents'
    foreach ($a in @($list.agents)) { if ([string]$a.agent_id -eq $AgentId) { return $a } }
    return $null
}

function Get-CsOnlineAgentId {
    $id = Get-CsStateValue 'agent_id'
    if (-not $id) { throw 'no agent_id recorded: the install phase did not enroll a Windows beacon' }
    $row = Get-CsAgentRow -AgentId $id
    if ($null -eq $row) { throw ('agent ' + $id + ' is not in GET /api/agents') }
    if ([string]$row.status -ne 'online') { throw ('agent ' + $id + ' is ' + $row.status + ', not online; last_seen=' + $row.last_seen) }
    return [string]$id
}

function Get-CsScmEvents {
    param([datetime]$Since, [int[]]$Ids = @())
    $filter = @{ LogName = 'System'; ProviderName = 'Service Control Manager'; StartTime = $Since }
    if ($Ids.Count -gt 0) { $filter['Id'] = $Ids }
    try {
        return @(Get-WinEvent -FilterHashtable $filter -ErrorAction Stop)
    } catch {
        # "No events" is the only error that means zero; anything else
        # (access denied, a broken log) must surface, not read as clean.
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { return @() }
        throw
    }
}

function Remove-CsMarker {
    if (Test-Path -LiteralPath $script:MarkerFile) { Remove-Item -LiteralPath $script:MarkerFile -Force }
}

function Get-CsParseErrors {
    param([string]$Path)
    $tokens = $null
    $fileErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$fileErrors)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $text = $script:Utf8NoBom.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    $tokens2 = $null
    $inputErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput($text, [ref]$tokens2, [ref]$inputErrors)
    $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $nonAscii = 0
    foreach ($b in $bytes) { if ($b -gt 0x7F) { $nonAscii++ } }
    return New-Object PSObject -Property @{
        FileErrors  = @($fileErrors)
        InputErrors = @($inputErrors)
        HasBom      = $hasBom
        NonAscii    = $nonAscii
        Bytes       = $bytes.Length
    }
}

function Format-CsParseError {
    param([object[]]$Errors)
    $list = @($Errors)
    if ($list.Count -eq 0) { return '' }
    return ('line ' + $list[0].Extent.StartLineNumber + ': ' + $list[0].Message + ' (' + $list.Count + ' error(s))')
}

# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

function Invoke-PhaseFloor {
    Set-CsStateValue 'job_started_utc' ((Get-Date).ToUniversalTime().ToString('o'))
    $ver = $PSVersionTable.PSVersion
    $edition = [string]$PSVersionTable['PSEdition']
    $os = @(Get-CimInstance -ClassName Win32_OperatingSystem)[0]
    $policies = (@(Get-ExecutionPolicy -List) | ForEach-Object { [string]$_.Scope + '=' + [string]$_.ExecutionPolicy }) -join '; '
    $record = [ordered]@{
        ps_version        = [string]$ver
        ps_edition        = $edition
        ps_build          = [string]$PSVersionTable['BuildVersion']
        clr_version       = [string]$PSVersionTable['CLRVersion']
        os_caption        = [string]$os.Caption
        os_version        = [string]$os.Version
        os_build          = [string]$os.BuildNumber
        os_sku            = [string]$os.OperatingSystemSKU
        ansi_code_page    = [System.Text.Encoding]::Default.CodePage
        oem_code_page     = [Console]::OutputEncoding.CodePage
        culture           = [System.Globalization.CultureInfo]::CurrentCulture.Name
        language_mode     = [string]$ExecutionContext.SessionState.LanguageMode
        elevated          = (Test-CsElevated)
        execution_policy  = $policies
        tls_default       = [string][System.Net.ServicePointManager]::SecurityProtocol
        computer_name     = $env:COMPUTERNAME
        user              = $env:USERNAME
    }
    Write-CsProof -Name 'floor.json' -Text (ConvertTo-Json -InputObject $record)
    foreach ($k in $record.Keys) { Write-CsInfo ($k + ' = ' + $record[$k]) }
    Assert-Cs ($edition -eq 'Desktop') 'PSEdition is Desktop (Windows PowerShell, not pwsh)' ('got ' + $edition)
    Assert-Cs (($ver.Major -eq 5) -and ($ver.Minor -eq 1)) 'PSVersion is 5.1 (the product floor)' ('got ' + $ver)
    Assert-Cs ([string]$ExecutionContext.SessionState.LanguageMode -eq 'FullLanguage') 'FullLanguage mode (CLM is out of scope for this job)'

    Add-CsSummary '## windows (native 5.1) - what this run proves'
    Add-CsSummary ''
    Add-CsSummary ('Runner: ' + $record.os_caption + ' build ' + $record.os_build + ', PowerShell ' + $record.ps_version + ' ' + $edition + ', ANSI cp' + $record.ansi_code_page + ', elevated=' + $record.elevated + '.')
    Add-CsSummary 'This is one hosted runner image, admin, cp1252, FullLanguage, http SimCore on loopback. It is not a customer host. tenant-verified: 0.'
    Add-CsSummary ''
    Add-CsSummary '| phase | result | failures |'
    Add-CsSummary '|---|---|---|'
}

function Invoke-PhaseGo {
    $kept = New-Object System.Collections.Generic.List[string]
    $dropped = New-Object System.Collections.Generic.List[string]
    foreach ($entry in ($env:PATH -split ';')) {
        if (-not $entry) { continue }
        if ($entry -match '\\Git\\(usr\\)?bin\\?$') { $dropped.Add($entry) } else { $kept.Add($entry) }
    }
    $env:PATH = $kept -join ';'
    Write-CsInfo ('removed from PATH: ' + ($dropped -join '; '))
    # A customer Windows host has no sh. With Git's on PATH, a test that
    # probes for sh would take the POSIX branch and prove nothing about the
    # Windows one.
    $sh = Get-Command sh -ErrorAction SilentlyContinue
    if ($null -ne $sh) { throw ('sh still resolves at ' + $sh.Source + ' after removing Git from PATH') }
    $go = Get-Command go -ErrorAction SilentlyContinue
    if ($null -eq $go) { throw 'go is not on PATH (actions/setup-go)' }
    if ($script:ProofDir -match '\s') { throw ('proof dir contains whitespace; the cmd redirection below is unquoted: ' + $script:ProofDir) }

    $vetFile = Join-Path $script:ProofDir 'go-vet.txt'
    $jsonFile = Join-Path $script:ProofDir 'go-test.json'
    $line = 'go version && go env GOOS GOARCH CGO_ENABLED && go vet ./... > ' + $vetFile + ' 2>&1 && go test ./... -count=1 -json > ' + $jsonFile + ' 2>&1'
    Push-Location -LiteralPath (Join-Path $script:Workspace 'agent')
    try { $code = Invoke-CsCmd -CommandLine $line } finally { Pop-Location }
    if (Test-Path -LiteralPath $vetFile) {
        $vet = [System.IO.File]::ReadAllText($vetFile)
        if ($vet.Trim()) { Write-Host '----- go vet -----'; Write-Host $vet }
    }
    Assert-Cs ($code -eq 0) 'go vet ./... && go test ./... -count=1 exit 0 on native windows/amd64' ('cmd exit ' + $code)
    if (-not (Test-Path -LiteralPath $jsonFile)) {
        Assert-Cs $false 'go-test.json was produced' 'go vet failed before go test ran; see go-vet.txt'
        return
    }

    $pass = 0; $fail = 0; $skip = 0; $nonJson = 0
    $psPass = New-Object System.Collections.Generic.List[string]
    $psSkip = New-Object System.Collections.Generic.List[string]
    $failed = New-Object System.Collections.Generic.List[string]
    $failedOutput = New-Object System.Collections.Generic.List[string]
    $outputByTest = @{}
    # Output events are most of the file and ConvertFrom-Json is slow on 5.1:
    # they are grouped by a cheap regex and decoded only for failing tests.
    $rxAction = [regex]'"Action":"(?<a>[a-z]+)"'
    $rxPackage = [regex]'"Package":"(?<p>[^"]*)"'
    $rxTest = [regex]'"Test":"(?<t>[^"]*)"'
    foreach ($raw in [System.IO.File]::ReadAllLines($jsonFile)) {
        $t = $raw.Trim()
        if (-not $t.StartsWith('{')) { if ($t) { $nonJson++ }; continue }
        $ma = $rxAction.Match($t)
        if (-not $ma.Success) { $nonJson++; continue }
        $action = $ma.Groups['a'].Value
        if ($action -eq 'output') {
            $mt = $rxTest.Match($t)
            if (-not $mt.Success) { continue }
            $okey = $rxPackage.Match($t).Groups['p'].Value + ' ' + $mt.Groups['t'].Value
            if (-not $outputByTest.ContainsKey($okey)) { $outputByTest[$okey] = New-Object System.Collections.Generic.List[string] }
            $outputByTest[$okey].Add($t)
            continue
        }
        if (@('pass', 'fail', 'skip') -notcontains $action) { continue }
        try { $ev = ConvertFrom-Json -InputObject $t } catch { $nonJson++; continue }
        $test = [string](Get-CsProp $ev 'Test')
        $key = [string](Get-CsProp $ev 'Package') + ' ' + $test
        if (-not $test) {
            if ($action -eq 'fail') { $failed.Add('package ' + [string](Get-CsProp $ev 'Package')) }
            continue
        }
        switch ($action) {
            'pass' { $pass++; if ($test -like 'TestPowerShell*') { $psPass.Add($test) } }
            'fail' {
                $fail++
                $failed.Add($key)
                if ($outputByTest.ContainsKey($key)) {
                    $failedOutput.Add('--- ' + $key + "`n")
                    foreach ($line in $outputByTest[$key]) {
                        try { $failedOutput.Add([string](ConvertFrom-Json -InputObject $line).Output) } catch { $failedOutput.Add($line + "`n") }
                    }
                }
            }
            'skip' { $skip++; if ($test -like 'TestPowerShell*') { $psSkip.Add($key) } }
        }
    }
    $summary = [ordered]@{
        pass = $pass; fail = $fail; skip = $skip; non_json_lines = $nonJson
        powershell_pass = ($psPass -join ';'); powershell_skip = ($psSkip -join ';'); failed = ($failed -join ';')
        stripped_path = ($dropped -join ';')
    }
    Write-CsProof -Name 'go-summary.json' -Text (ConvertTo-Json -InputObject $summary -Depth 3)
    Write-CsInfo ('go test: pass=' + $pass + ' fail=' + $fail + ' skip=' + $skip + ' non-json=' + $nonJson)
    if ($failedOutput.Count -gt 0) {
        Write-Host '----- failing test output -----'
        foreach ($l in $failedOutput) { Write-Host -NoNewline $l }
        Write-Host ''
    }
    Assert-Cs (($pass + $fail) -gt 0) 'go test reported results (a zero is degraded, not ok)'
    Assert-Cs ($fail -eq 0 -and $failed.Count -eq 0) 'no go test failures' ($failed -join '; ')
    Assert-Cs ($psSkip.Count -eq 0) 'no TestPowerShell* test skipped on Windows' ($psSkip -join '; ')
    Assert-Cs ($psPass.Count -gt 0) 'at least one TestPowerShell* test executed and passed' 'none ran: the PowerShell exec path was not exercised'
}

function Invoke-PhaseBuild {
    $rows = New-Object System.Collections.ArrayList
    foreach ($name in $script:DistTargets) {
        $path = Join-Path $script:AgentDist $name
        $ok = (Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Length -gt 0)
        Assert-Cs $ok ('shipped recipe produced ' + $name)
    }
    $exe = Join-Path $script:AgentDist $script:WinExe
    if (-not (Test-Path -LiteralPath $exe)) { return }

    $bytes = [System.IO.File]::ReadAllBytes($exe)
    $isMz = ($bytes.Length -gt 0x40 -and $bytes[0] -eq 0x4D -and $bytes[1] -eq 0x5A)
    Assert-Cs $isMz ($script:WinExe + ' starts with MZ')
    if (-not $isMz) { return }
    $peOff = [BitConverter]::ToInt32($bytes, 0x3C)
    $inRange = ($peOff -gt 0 -and ($peOff + 24 + 70) -lt $bytes.Length)
    Assert-Cs $inRange 'e_lfanew points inside the file' ('e_lfanew=' + $peOff)
    if (-not $inRange) { return }
    $isPe = ($bytes[$peOff] -eq 0x50 -and $bytes[$peOff + 1] -eq 0x45 -and $bytes[$peOff + 2] -eq 0 -and $bytes[$peOff + 3] -eq 0)
    $machine = [BitConverter]::ToUInt16($bytes, $peOff + 4)
    $magic = [BitConverter]::ToUInt16($bytes, $peOff + 24)
    $subsystem = [BitConverter]::ToUInt16($bytes, $peOff + 24 + 68)
    Assert-Cs $isPe 'PE signature present'
    Assert-Cs ($machine -eq 0x8664) 'PE machine is 0x8664 (x86-64)' ('got 0x' + $machine.ToString('X4'))
    Assert-Cs ($magic -eq 0x20B) 'optional header is PE32+' ('got 0x' + $magic.ToString('X4'))
    Assert-Cs ($subsystem -eq 3) 'subsystem is WINDOWS_CUI (console)' ('got ' + $subsystem)

    $sig = Get-AuthenticodeSignature -FilePath $exe
    $sigStatus = [string]$sig.Status
    Write-CsInfo ('Authenticode: ' + $sigStatus + ' (' + $sig.StatusMessage + ')')
    # Unsigned today. Valid would be fine; anything else means a signature
    # that is present and broken.
    Assert-Cs ($sigStatus -eq 'NotSigned' -or $sigStatus -eq 'Valid') 'Authenticode status is NotSigned (or Valid)' ('got ' + $sigStatus)

    $sums = Join-Path $script:AgentDist 'SHA256SUMS'
    Assert-Cs (Test-Path -LiteralPath $sums) 'SHA256SUMS written by the recipe'
    $listed = @{}
    if (Test-Path -LiteralPath $sums) {
        foreach ($line in [System.IO.File]::ReadAllLines($sums)) {
            $m = [regex]::Match($line, '^(?<h>[0-9a-fA-F]{64})\s+\*?(?<n>\S+)\s*$')
            if ($m.Success) { $listed[$m.Groups['n'].Value] = $m.Groups['h'].Value.ToLower() }
        }
    }
    foreach ($name in $script:DistTargets) {
        $path = Join-Path $script:AgentDist $name
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower()
        $want = ''
        if ($listed.ContainsKey($name)) { $want = $listed[$name] }
        Assert-Cs ($want -eq $hash) ('SHA256SUMS matches ' + $name) ('listed=' + $want + ' actual=' + $hash)
        [void]$rows.Add([ordered]@{ file = $name; bytes = (Get-Item -LiteralPath $path).Length; sha256 = $hash })
    }
    $record = [ordered]@{
        exe = $script:WinExe; pe_machine = ('0x' + $machine.ToString('X4')); pe_magic = ('0x' + $magic.ToString('X4'))
        subsystem = $subsystem; authenticode = $sigStatus
    }
    Write-CsProof -Name 'build.json' -Text (ConvertTo-Json -InputObject $record)
    Write-CsProof -Name 'agent-dist.json' -Text (ConvertTo-CsJsonArray -Items $rows.ToArray())
}

function Invoke-PhaseRender {
    $renderer = Join-Path $script:Workspace 'scripts\ci\render_powershell.py'
    if (-not (Test-Path -LiteralPath $renderer)) {
        throw 'scripts/ci/render_powershell.py is missing: the powershell-gate lane has not been integrated'
    }
    New-Item -ItemType Directory -Force -Path $script:RenderDir | Out-Null
    $py = (Get-Command python -ErrorAction Stop).Source
    $env:CORTEXSIM_BASE_DIR = $script:Workspace
    $env:PYTHONPATH = Join-Path $script:Workspace 'core'
    $child = Start-CsChild -FilePath $py -Arguments ('"' + $renderer + '" --out "' + $script:RenderDir + '"') -Name 'render'
    Assert-Cs ($child.ExitCode -eq 0) 'render_powershell.py exit 0' ('exit ' + $child.ExitCode)
    $files = @(Get-ChildItem -LiteralPath $script:RenderDir -Recurse -File | Where-Object { $_.Extension -eq '.ps1' -or $_.Extension -eq '.psm1' })
    Write-CsProof -Name 'render-manifest.txt' -Text ((@($files) | ForEach-Object { $_.FullName.Substring($script:RenderDir.Length) }) -join "`n")
    Assert-Cs ($files.Count -gt 0) 'render produced at least one PowerShell file (a zero is degraded, not ok)' ('found ' + $files.Count)
}

function Get-CsRenderedFiles {
    if (-not (Test-Path -LiteralPath $script:RenderDir)) { throw ('no render output at ' + $script:RenderDir) }
    $files = @(Get-ChildItem -LiteralPath $script:RenderDir -Recurse -File | Where-Object { $_.Extension -eq '.ps1' -or $_.Extension -eq '.psm1' })
    if ($files.Count -eq 0) { throw ('no .ps1/.psm1 under ' + $script:RenderDir) }
    return $files
}

function Invoke-PhaseParse {
    $rows = New-Object System.Collections.ArrayList
    foreach ($f in @(Get-CsRenderedFiles)) {
        $rel = $f.FullName.Substring($script:RenderDir.Length).TrimStart('\')
        $r = Get-CsParseErrors -Path $f.FullName
        Assert-Cs ($r.FileErrors.Count -eq 0) ('ParseFile (as -File reads it; BOM=' + $r.HasBom + '): ' + $rel) (Format-CsParseError $r.FileErrors)
        Assert-Cs ($r.InputErrors.Count -eq 0) ('ParseInput on the UTF-8 text (as iwr | iex reads it): ' + $rel) (Format-CsParseError $r.InputErrors)
        [void]$rows.Add([ordered]@{
            file = $rel; bytes = $r.Bytes; bom = $r.HasBom; non_ascii_bytes = $r.NonAscii
            parsefile_errors = $r.FileErrors.Count; parseinput_errors = $r.InputErrors.Count
            first_error = (Format-CsParseError (@($r.FileErrors) + @($r.InputErrors)))
        })
    }
    Write-CsProof -Name 'parse.json' -Text (ConvertTo-CsJsonArray -Items $rows.ToArray())
    $controls = @(Invoke-CsParseControls)
    Write-CsProof -Name 'parse-controls.json' -Text (ConvertTo-CsJsonArray -Items $controls)
}

# Controls for the parse phase. The loop above only ever asserts ZERO errors,
# so a parser that could reject nothing (a wrong decode, an empty read, a
# powershell.exe that is not 5.1 Desktop) would report the same green. These
# three fixtures have a known verdict under Windows PowerShell 5.1 and two of
# them MUST be rejected:
#   good_51.ps1           clean as a file and as UTF-8 text
#   bad_ps7_only.ps1      PowerShell-7-only syntax (ASCII): 5.1 cannot parse it
#   bad_cp1252_quote.ps1  BOM-less UTF-8 em dash inside a double-quoted string:
#                         ParseFile reads the file in the ANSI code page, where
#                         the dash's third byte (0x94) is a closing quote, so
#                         the file read must fail; the UTF-8 text (what
#                         iwr | iex hands the parser) must parse.
# scripts/ci/render_powershell.py excludes the two bad files from the rendered
# set by exact path, so this is the only place in the job that reads them. A
# missing fixture throws: controls over zero files prove nothing.
function Invoke-CsParseControls {
    param([string]$FixtureDir = '')
    if (-not $FixtureDir) { $FixtureDir = Join-Path $script:Workspace 'tests\ci\fixtures' }
    $ansi = [System.Text.Encoding]::Default.CodePage
    $controls = @(
        @{ file = 'good_51.ps1'; file_rejected = $false; input_rejected = $false },
        @{ file = 'bad_ps7_only.ps1'; file_rejected = $true; input_rejected = $true },
        @{ file = 'bad_cp1252_quote.ps1'; file_rejected = $true; input_rejected = $false }
    )
    $rows = New-Object System.Collections.ArrayList
    foreach ($c in $controls) {
        $name = [string]$c.file
        $path = Join-Path $FixtureDir $name
        if (-not (Test-Path -LiteralPath $path)) { throw ('parse control fixture is missing: ' + $path) }
        $r = Get-CsParseErrors -Path $path
        $checks = @(
            @{ what = 'ParseFile (as -File reads it)'; want = [bool]$c.file_rejected; errors = @($r.FileErrors) },
            @{ what = 'ParseInput on the UTF-8 text (as iwr | iex reads it)'; want = [bool]$c.input_rejected; errors = @($r.InputErrors) }
        )
        foreach ($k in $checks) {
            $found = @($k.errors).Count
            $rejected = ($found -gt 0)
            if ($k.want) { $expect = 'expected it to be REJECTED' } else { $expect = 'expected it to parse' }
            $detail = $expect + ', the parser reported ' + $found + ' error(s); ANSI cp' + $ansi
            if ($found -gt 0) { $detail = $detail + '; ' + (Format-CsParseError $k.errors) }
            if ($k.want) { $verdict = 'rejects' } else { $verdict = 'accepts' }
            Assert-Cs ($rejected -eq $k.want) ('control: ' + $k.what + ' ' + $verdict + ' ' + $name) $detail
        }
        [void]$rows.Add([ordered]@{
            file = $name; bom = $r.HasBom; non_ascii_bytes = $r.NonAscii; ansi_code_page = $ansi
            expect_file_rejected = [bool]$c.file_rejected; parsefile_errors = @($r.FileErrors).Count
            expect_input_rejected = [bool]$c.input_rejected; parseinput_errors = @($r.InputErrors).Count
        })
    }
    return $rows.ToArray()
}

function Invoke-PhasePssa {
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor [System.Net.SecurityProtocolType]::Tls12
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser -WarningAction SilentlyContinue | Out-Null
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted
    # TODO(pssa-pin): CORTEXSIM_PSSA_VERSION is empty in ci.yml because no
    # PSGallery lookup was done when this was authored. Until it is pinned,
    # the newest PSScriptAnalyzer is installed and its version recorded in
    # pssa-meta.json; pin ci.yml to that value after the first green run.
    $pin = [string]$env:CORTEXSIM_PSSA_VERSION
    if ($pin) {
        Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -RequiredVersion $pin -WarningAction SilentlyContinue
        Import-Module -Name PSScriptAnalyzer -RequiredVersion $pin
    } else {
        Write-CsInfo 'UNPINNED: CORTEXSIM_PSSA_VERSION is empty; installing the newest PSScriptAnalyzer (see TODO(pssa-pin))'
        Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -WarningAction SilentlyContinue
        Import-Module -Name PSScriptAnalyzer
    }
    $module = @(Get-Module -Name PSScriptAnalyzer)[0]
    $profileDir = Join-Path $module.ModuleBase 'compatibility_profiles'
    $all = @(Get-ChildItem -LiteralPath $profileDir -Filter 'win-*_5.1.*_framework.json' -ErrorAction SilentlyContinue)
    # Server 2016 is 10.0.14393, Server 2019 is 10.0.17763.
    $profiles = @($all | Where-Object { $_.Name -match '_10\.0\.(14393|17763)\.' } | ForEach-Object { $_.BaseName })
    if ($profiles.Count -eq 0) {
        throw ('no Server 2016/2019 Windows PowerShell 5.1 profile in ' + $profileDir + '; found: ' + ((@($all) | ForEach-Object { $_.Name }) -join ', '))
    }
    Write-CsInfo ('PSScriptAnalyzer ' + $module.Version + ' profiles: ' + ($profiles -join ', '))
    $meta = [ordered]@{ version = [string]$module.Version; pinned = [bool]$pin; profiles = ($profiles -join ';') }
    Write-CsProof -Name 'pssa-meta.json' -Text (ConvertTo-Json -InputObject $meta)

    $settings = @{
        Rules = @{
            PSUseCompatibleSyntax   = @{ Enable = $true; TargetVersions = @('5.1') }
            PSUseCompatibleCommands = @{ Enable = $true; TargetProfiles = $profiles; IgnoreCommands = @('Invoke-ScriptAnalyzer') }
            PSUseCompatibleTypes    = @{ Enable = $true; TargetProfiles = $profiles }
        }
    }
    $targets = New-Object System.Collections.Generic.List[string]
    foreach ($f in @(Get-CsRenderedFiles)) { $targets.Add($f.FullName) }
    $targets.Add((Join-Path $script:Workspace 'scripts\ci\windows-native-e2e.ps1'))

    # Reviewed waivers, from the first run of this job (2026-11-10, PSScriptAnalyzer
    # 1.25.0). Each names ONE rule, ONE file kind and ONE message pattern, and says
    # why the finding is not a Windows PowerShell 5.1 defect. Every other
    # PSUseCompatible* / Error / ParseError finding still fails the phase. A waiver
    # that matches nothing is reported as stale so this list cannot rot quietly.
    $waivers = @(
        @{
            id = 'installer-acl-extension-branch'; rule = 'PSUseCompatibleTypes'
            path = '*\installer\windows-*.ps1'; message = '*''IO.FileSystemAclExtensions''*'
            why = 'reached only when [IO.Directory] has no two-argument CreateDirectory (.NET Core); 5.1 takes the other branch, which the install phase executes'
        },
        @{
            id = 'step-names-its-target-host'; rule = 'PSAvoidUsingComputerNameHardcoded'
            path = '*\steps\*\*.ps1'; message = '*'
            why = 'a scenario step names its target host by design; a sharing-hygiene rule, not a 5.1 compatibility finding'
        }
    )
    $waived = @{}

    $records = New-Object System.Collections.ArrayList
    $failing = 0
    $tolerated = @{}
    foreach ($path in $targets) {
        $found = @(Invoke-ScriptAnalyzer -Path $path -Settings $settings)
        foreach ($d in $found) {
            $sev = [string]$d.Severity
            $rule = [string]$d.RuleName
            $isFailure = ($rule -like 'PSUseCompatible*') -or ($sev -eq 'Error') -or ($sev -eq 'ParseError')
            $waivedBy = ''
            if ($isFailure) {
                foreach ($w in $waivers) {
                    if (($rule -eq $w.rule) -and ($path -like $w.path) -and ([string]$d.Message -like $w.message)) {
                        $waivedBy = [string]$w.id
                        break
                    }
                }
                if ($waivedBy) {
                    $isFailure = $false
                    if (-not $waived.ContainsKey($waivedBy)) { $waived[$waivedBy] = 0 }
                    $waived[$waivedBy] = $waived[$waivedBy] + 1
                }
            }
            $line = 0
            if ($null -ne $d.Extent) { $line = $d.Extent.StartLineNumber }
            [void]$records.Add([ordered]@{
                file = $path; line = $line; rule = $rule; severity = $sev; failing = $isFailure; waived = $waivedBy; message = [string]$d.Message
            })
            if ($waivedBy) { continue }
            if ($isFailure) {
                $failing++
                Assert-Cs $false ($rule + ' [' + $sev + '] ' + (Split-Path -Leaf $path) + ':' + $line) (Limit-Cs ([string]$d.Message) 300)
            } else {
                if (-not $tolerated.ContainsKey($rule)) { $tolerated[$rule] = 0 }
                $tolerated[$rule] = $tolerated[$rule] + 1
            }
        }
    }
    Write-CsProof -Name 'pssa.json' -Text (ConvertTo-CsJsonArray -Items $records.ToArray())
    # Expected and NOT failures: PSAvoidAssignmentToAutomaticVariable (the
    # installer's $Args), PSUseApprovedVerbs (Cs-*), PSAvoidUsingWriteHost.
    foreach ($rule in @($tolerated.Keys | Sort-Object)) { Write-CsInfo ('tolerated: ' + $rule + ' x' + $tolerated[$rule]) }
    foreach ($w in $waivers) {
        if ($waived.ContainsKey([string]$w.id)) {
            Write-CsInfo ('WAIVED: ' + $w.rule + ' x' + $waived[[string]$w.id] + ' (' + $w.id + ') -- ' + $w.why)
        } else {
            Write-CsInfo ('STALE WAIVER: ' + $w.id + ' matched nothing; remove it from the pssa phase')
        }
    }
    Write-CsInfo ('PSScriptAnalyzer: ' + $targets.Count + ' file(s), ' + $records.Count + ' record(s), ' + $failing + ' failing')
    Assert-Cs ($failing -eq 0) 'no PSUseCompatible* / Error / ParseError findings'
}

function Add-CsGithubEnv {
    param([string]$Name, [string]$Value)
    Set-Item -LiteralPath ('env:' + $Name) -Value $Value
    if ($env:GITHUB_ENV) {
        [System.IO.File]::AppendAllText($env:GITHUB_ENV, ($Name + '=' + $Value + "`n"), $script:Utf8NoBom)
    }
}

function Invoke-PhaseBoot {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $secret = -join (@($bytes) | ForEach-Object { $_.ToString('x2') })
    Write-Host ('::add-mask::' + $secret)
    Add-CsGithubEnv -Name 'CORTEXSIM_BASE_DIR' -Value $script:Workspace
    Add-CsGithubEnv -Name 'CORTEXSIM_AGENT_DIST' -Value $script:AgentDist
    Add-CsGithubEnv -Name 'CORTEXSIM_SECRET' -Value $secret

    $py = (Get-Command python -ErrorAction Stop).Source
    $out = Join-Path $script:ProofDir 'simcore.stdout.log'
    $err = Join-Path $script:ProofDir 'simcore.stderr.log'
    # Through cmd.exe with no -Redirect*: Start-Process then uses ShellExecute,
    # so the child does NOT inherit this powershell.exe's handles. With
    # -RedirectStandardOutput it is CreateProcess(bInheritHandles=TRUE) and
    # uvicorn would hold the runner's step stdout/stderr pipe open, which can
    # keep the step from ending. The file redirection is cmd's; the outer
    # quote pair is the one cmd /c strips. The pid is cmd.exe's: collect
    # stops the tree with taskkill /T.
    $cmdArgs = '/d /c ""' + $py + '" -m uvicorn main:app --app-dir core --host 127.0.0.1 --port 8888 > "' + $out + '" 2> "' + $err + '""'
    $proc = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\cmd.exe') -ArgumentList $cmdArgs `
        -WorkingDirectory $script:Workspace -PassThru -WindowStyle Hidden
    try { $null = $proc.Handle } catch { Write-CsInfo ('could not open a SimCore process handle: ' + $_.Exception.Message) }
    Set-CsStateValue 'simcore_pid' $proc.Id
    Write-CsInfo ('SimCore pid ' + $proc.Id + ' (' + $py + ')')

    $health = $null
    $deadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { break }
        $r = Invoke-Cs -Path '/api/health' -TimeoutSec 5
        if ($r.Status -eq 200 -and $null -ne $r.Json) { $health = $r; break }
        Start-Sleep -Seconds 2
    }
    if ($null -eq $health) {
        $tail = ''
        if (Test-Path -LiteralPath $err) { $tail = Limit-Cs (Get-Content -LiteralPath $err -Tail 60 | Out-String) 4000 }
        throw ('SimCore did not answer /api/health within 120 s (exited=' + $proc.HasExited + '). stderr tail: ' + $tail)
    }
    Write-CsProof -Name 'health.json' -Text $health.Text
    $components = $health.Json.components
    foreach ($name in $script:HealthRequired) {
        $c = Get-CsProp $components $name
        if ($null -eq $c) { Assert-Cs $false ('/api/health reports component ' + $name); continue }
        Assert-Cs ([string]$c.status -eq 'ok') ('/api/health ' + $name + ' ok') ([string](Get-CsProp $c 'code') + ': ' + (Limit-Cs ([string](Get-CsProp $c 'detail')) 300))
    }
    foreach ($prop in $components.PSObject.Properties) {
        if ($script:HealthRequired -contains $prop.Name) { continue }
        Write-CsInfo ('health ' + $prop.Name + ' = ' + $prop.Value.status + ' ' + [string](Get-CsProp $prop.Value 'code') + ' (recorded, not gated)')
    }

    $bins = Invoke-CsJson -Path '/api/agents/binaries' -SaveAs 'agents-binaries.json'
    $win = @(@($bins.binaries) | Where-Object { $_.os -eq 'windows' -and $_.arch -eq 'amd64' })
    Assert-Cs ($win.Count -eq 1) 'GET /api/agents/binaries lists windows/amd64' ('found ' + $win.Count)
    Assert-Cs ([string]$bins.dist_dir -eq $script:AgentDist) 'SimCore serves the dist this job built' ('dist_dir=' + $bins.dist_dir)
    if ($win.Count -eq 1) {
        $local = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $script:AgentDist $script:WinExe)).Hash.ToLower()
        Assert-Cs ([string]$win[0].sha256 -eq $local) 'served windows/amd64 sha256 equals Get-FileHash of the built exe' ('served=' + $win[0].sha256 + ' local=' + $local)
    }
}

function Invoke-PhaseInstall {
    Assert-Cs (Test-CsElevated) 'job runs elevated (hosted runner admin); the service path needs it'
    $started = Get-Date
    Set-CsStateValue 'install_started_utc' ($started.ToUniversalTime().ToString('o'))

    $minted = Invoke-CsJson -Method POST -Path '/api/agents/enroll/tokens' `
        -BodyBytes (ConvertTo-CsBody @{ label = 'ci-windows-native-e2e'; ttl_seconds = 900; max_uses = 1 })
    $tok = [string]$minted.token
    if (-not $tok.StartsWith('cxs_')) { throw 'POST /api/agents/enroll/tokens did not reveal a cxs_ token' }
    Write-Host ('::add-mask::' + $tok)

    # The bytes iex will run, parsed the way iex parses them.
    $installer = Invoke-Cs -Path '/api/agents/install?os=windows'
    Assert-Cs ($installer.Status -eq 200) 'GET /api/agents/install?os=windows 200' ('got ' + $installer.Status)
    Write-CsProof -Name 'installer.ps1.txt' -Text $installer.Text
    $tokens = $null; $perr = $null
    [void][System.Management.Automation.Language.Parser]::ParseInput($installer.Text, [ref]$tokens, [ref]$perr)
    Assert-Cs (@($perr).Count -eq 0) 'installer parses as iwr | iex receives it' (Format-CsParseError @($perr))

    # The console one-liner, verbatim apart from the quote style around the
    # URL (it carries no $ or backtick, so single and double are identical).
    $oneLiner = '$env:CORTEXSIM_TOKEN=''' + $tok + '''; iwr -useb ''' + $Server + '/api/agents/install?os=windows'' | iex'
    $child = Start-CsChild -FilePath $script:WinPs -Arguments ('-NoProfile -ExecutionPolicy Bypass -Command "' + $oneLiner + '"') -Name 'install'
    Assert-Cs ($child.ExitCode -eq 0) 'install one-liner exit 0' ('exit=' + $child.ExitCode + ' timed_out=' + $child.TimedOut)

    $agents = @((Invoke-CsJson -Path '/api/agents' -SaveAs 'agents-after-install.json').agents)
    $mine = @($agents | Where-Object { $_.os -eq 'windows' -and $_.hostname -eq $env:COMPUTERNAME })
    Assert-Cs ($mine.Count -eq 1) ('exactly one agent with os=windows hostname=' + $env:COMPUTERNAME) ('found ' + $mine.Count)
    if ($mine.Count -ge 1) { Set-CsStateValue 'agent_id' ([string]$mine[0].agent_id) }
    $attempts = @((Invoke-CsJson -Path '/api/agents/install/attempts' -SaveAs 'install-attempts-after-install.json').attempts)
    $lastStage = ''; $lastCode = ''
    if ($attempts.Count -gt 0) { $lastStage = [string]$attempts[0].stage; $lastCode = [string]$attempts[0].code }
    Assert-Cs ($lastStage -eq 'install' -and $lastCode -eq 'OK') 'last install attempt is install/OK' ('got ' + $lastStage + '/' + $lastCode)

    # 75 s covers the SCM start timeout (30 s), a restart action (10 s) and a
    # second failed start: a beacon that does not speak the SCM protocol is
    # not Running by then, whatever sc.exe start printed.
    Start-Sleep -Seconds 75
    $svc = Get-Service -Name $script:ServiceName -ErrorAction SilentlyContinue
    Assert-Cs ($null -ne $svc) ('service ' + $script:ServiceName + ' exists')
    if ($null -ne $svc) {
        Set-CsStateValue 'service_seen' $true
        Assert-Cs ($svc.Status.ToString() -eq 'Running') ('service ' + $script:ServiceName + ' is Running 75 s after install') ('status=' + $svc.Status)
    }
    $bad = @(Get-CsScmEvents -Since $started -Ids @(7000, 7009, 7011, 7031, 7034) | Where-Object { $_.Message -match 'CortexSim' })
    $badText = (@($bad) | ForEach-Object { [string]$_.Id + ': ' + $_.Message }) -join ' | '
    Assert-Cs ($bad.Count -eq 0) 'no SCM 7000/7009/7011/7031/7034 events for the CortexSim service' (Limit-Cs $badText 800)

    $agentId = Get-CsStateValue 'agent_id'
    if ($agentId) {
        $a1 = Get-CsAgentRow -AgentId $agentId
        Start-Sleep -Seconds 20
        $a2 = Get-CsAgentRow -AgentId $agentId
        $s1 = ''; $s2 = ''; $t1 = $null; $t2 = $null
        if ($null -ne $a1) { $s1 = [string]$a1.status; $t1 = ConvertTo-CsUtc ([string]$a1.last_seen) }
        if ($null -ne $a2) { $s2 = [string]$a2.status; $t2 = ConvertTo-CsUtc ([string]$a2.last_seen) }
        Assert-Cs ($s1 -eq 'online' -and $s2 -eq 'online') 'agent reads online in two samples 20 s apart' ('got ' + $s1 + ', ' + $s2)
        Assert-Cs (($null -ne $t1) -and ($null -ne $t2) -and ($t2 -gt $t1)) 'last_seen advanced between the samples (beaconing, not merely enrolled)' ('t1=' + $t1 + ' t2=' + $t2)
    }

    $log = Join-Path $script:InstallDir 'agent.log'
    $hasLog = Test-Path -LiteralPath $log
    Assert-Cs $hasLog ('beacon log exists: ' + $log)
    if ($hasLog) {
        Assert-Cs ((Get-Item -LiteralPath $log).Length -gt 0) 'beacon log is non-empty'
        try { Copy-Item -LiteralPath $log -Destination (Join-Path $script:ProofDir 'agent.after-install.log') -Force } catch { Write-CsInfo ('could not copy agent.log: ' + $_.Exception.Message) }
    }
}

function Invoke-PhasePull {
    $agentId = Get-CsOnlineAgentId

    # --- probe: step-01 rc 0, step-04 stderr + rc 0, step-02 rc 3, step-03 not_run
    Remove-CsMarker
    $probe = New-CsDraft -Path (Get-CsFixtureDraftPath -Role 'probe') -Key 'probe'
    $launched = (Get-Date).ToUniversalTime()
    $runId = Start-CsPullRun -ScenarioId $probe -AgentId $agentId -Key 'probe'
    $run = Wait-CsRun -RunId $runId -TimeoutSec 180 -SaveAs 'run-pull-probe.json'
    $status = ''
    if ($null -ne $run) { $status = [string]$run.status }
    Assert-Cs (@('complete', 'failed', 'aborted') -contains $status) 'probe run reached a terminal state within 180 s' ('status=' + $status)
    if ($null -ne $run) {
        $map = Get-CsOutcomeMap $run
        Assert-Cs (@($run.step_outcomes).Count -eq 4) 'one step_outcomes row per probe step' ('rows=' + @($run.step_outcomes).Count)
        Assert-CsOutcome $map 'step-01' 'exited' 0
        Assert-CsOutcome $map 'step-04' 'exited' 0
        Assert-CsOutcome $map 'step-02' 'exited' 3
        # Fail-fast, exactly as on POSIX: the step after the first non-zero
        # exit is recorded not_run / UPSTREAM_FAILED and never executed.
        Assert-CsOutcome $map 'step-03' 'not_run' $null 'UPSTREAM_FAILED'
        Assert-Cs ($status -eq 'failed') 'probe run status is failed (a run with a non-zero step never reads complete)' ('status=' + $status)
        $output = [string](Get-CsProp $run 'output')
        Write-CsProof -Name 'run-pull-probe.output.txt' -Text $output
        $blocks = Get-CsStepBlocks -Output $output
        $b1 = ''; $b4 = ''
        if ($blocks.ContainsKey('step-01')) { $b1 = $blocks['step-01'] }
        if ($blocks.ContainsKey('step-04')) { $b4 = $blocks['step-04'] }
        Assert-Cs ($b1.Contains('cortexsim-ci-marker')) 'step-01 output carries the marker string'
        Assert-Cs ($b4.Contains('progress')) 'step-04 stderr reached the run output'
        Assert-Cs (-not $output.Contains('#< CLIXML')) 'stderr is plain text, not PowerShell CLIXML'
        Assert-Cs (-not $blocks.ContainsKey('step-03')) 'step-03 produced no output (it never ran)'
    }
    $markerOk = Test-Path -LiteralPath $script:MarkerFile
    Assert-Cs $markerOk ('step-01 wrote ' + $script:MarkerFile + ' on disk')
    if ($markerOk) {
        $item = Get-Item -LiteralPath $script:MarkerFile
        Assert-Cs ($item.LastWriteTimeUtc -ge $launched.AddSeconds(-2)) 'marker was written by this run' ('mtime=' + $item.LastWriteTimeUtc.ToString('o'))
        Assert-Cs (([System.IO.File]::ReadAllText($script:MarkerFile)).Trim() -eq 'ok') 'marker content is ok'
    }
    Remove-CsMarker

    # --- cmdlet_error: a non-terminating cmdlet error must exit non-zero
    $cmdlet = New-CsDraft -Path (Get-CsFixtureDraftPath -Role 'cmdlet_error') -Key 'cmdlet_error'
    $runId2 = Start-CsPullRun -ScenarioId $cmdlet -AgentId $agentId -Key 'cmdlet_error'
    $run2 = Wait-CsRun -RunId $runId2 -TimeoutSec 180 -SaveAs 'run-pull-cmdlet-error.json'
    $status2 = ''
    if ($null -ne $run2) { $status2 = [string]$run2.status }
    Assert-Cs ($status2 -eq 'failed') 'cmdlet-error run status is failed' ('status=' + $status2)
    if ($null -ne $run2) {
        $map2 = Get-CsOutcomeMap $run2
        Assert-CsOutcome $map2 'step-03' 'exited' 'nonzero'
        $output2 = [string](Get-CsProp $run2 'output')
        Write-CsProof -Name 'run-pull-cmdlet-error.output.txt' -Text $output2
        Assert-Cs ($output2.Contains('cortexsim-no-such-process')) 'the cmdlet error text reached the run output'
        Assert-Cs (-not $output2.Contains('#< CLIXML')) 'cmdlet error is plain text, not CLIXML'
    }
}

function Invoke-PhaseAbort {
    $agentId = Get-CsOnlineAgentId
    $root = Get-CsBeaconRootPid
    if ($root -le 0) { throw 'no running beacon process (service or detached) to walk' }
    Write-CsInfo ('beacon root pid ' + $root)
    $sid = New-CsDraft -Path (Get-CsFixtureDraftPath -Role 'abort') -Key 'abort'
    $launched = Get-Date
    $runId = Start-CsPullRun -ScenarioId $sid -AgentId $agentId -Key 'abort'

    # The abort assertion is vacuous unless the step demonstrably started.
    $before = $null
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline) {
        $desc = @(Get-CsDescendants -RootPid $root)
        if (@($desc | Where-Object { $_.Name -eq 'ping.exe' }).Count -gt 0) { $before = $desc; break }
        Start-Sleep -Seconds 2
    }
    Assert-Cs ($null -ne $before) 'the beacon started the step: ping.exe is a descendant of the beacon process'
    if ($null -eq $before) {
        $null = Invoke-Cs -Method POST -Path ('/api/runs/' + $runId + '/abort')
        return
    }
    $stepProcs = @($before | Where-Object { $_.Name -eq 'ping.exe' -or $_.Name -eq 'powershell.exe' })
    Write-CsInfo ('step tree before abort: ' + (Format-CsProcs $before))

    # An unrelated long-running ping, not under the beacon: tree-kill must not
    # reach it (BEACON-04).
    $control = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\PING.EXE') -ArgumentList '-n 600 127.0.0.1' -PassThru -WindowStyle Hidden
    Set-CsStateValue 'control_ping_pid' $control.Id
    $controlInfo = @(Get-CimInstance -ClassName Win32_Process -Filter ('ProcessId=' + $control.Id))[0]

    $elapsed = ((Get-Date) - $launched).TotalSeconds
    if ($elapsed -lt 10) { Start-Sleep -Seconds ([int][Math]::Ceiling(10 - $elapsed)) }
    $abortAt = Get-Date
    $null = Invoke-CsJson -Method POST -Path ('/api/runs/' + $runId + '/abort') -SaveAs 'abort-response.json'

    $clean = $false
    $left = @()
    $alive = @()
    while (((Get-Date) - $abortAt).TotalSeconds -lt 15) {
        $left = @(Get-CsDescendants -RootPid $root | Where-Object { $_.Name -eq 'ping.exe' -or $_.Name -eq 'powershell.exe' })
        $alive = @($stepProcs | Where-Object { Test-CsSameProcess $_ })
        if ($left.Count -eq 0 -and $alive.Count -eq 0) { $clean = $true; break }
        Start-Sleep -Milliseconds 500
    }
    $took = [Math]::Round(((Get-Date) - $abortAt).TotalSeconds, 1)
    Assert-Cs $clean ('within 15 s of POST /abort no ping.exe / powershell.exe remains in the beacon tree (' + $took + ' s)') ('left: ' + (Format-CsProcs $left) + '; still alive: ' + (Format-CsProcs $alive))
    Assert-Cs (Test-CsSameProcess $controlInfo) 'an unrelated ping -n 600 started by the job is still alive (tree-kill stays inside the step)'
    try { Stop-Process -Id $control.Id -Force -ErrorAction Stop } catch { Write-CsInfo ('control ping already gone: ' + $_.Exception.Message) }

    $run = Wait-CsRun -RunId $runId -TimeoutSec 60 -SaveAs 'run-pull-abort.json'
    $status = ''
    if ($null -ne $run) { $status = [string]$run.status }
    Assert-Cs ($status -eq 'aborted') 'abort run status is aborted' ('status=' + $status)
    # Not asserted: step-01's termination. POST /abort makes the run terminal
    # at once and complete_run ignores the beacon's later /complete by design,
    # so the step_outcomes row keeps termination=null. Recorded, not gated.
    if ($null -ne $run) {
        foreach ($row in @($run.step_outcomes)) {
            Write-CsInfo ('abort step_outcome ' + $row.step_id + ' termination=' + (Get-CsProp $row 'termination') + ' rc=' + (Get-CsProp $row 'rc'))
        }
    }
}

function Invoke-PhasePush {
    $sid = New-CsDraft -Path (Get-CsFixtureDraftPath -Role 'probe') -Key 'probe'
    $bundle = Join-Path $script:ProofDir ('cortexsim-' + $sid + '.ps1')
    $r = Invoke-Cs -Path ('/api/scenarios/' + $sid + '/download?format=powershell') -OutFile $bundle -TimeoutSec 60
    Assert-Cs ($r.Status -eq 200) 'GET /download?format=powershell 200' ('got ' + $r.Status + ' ' + $r.Error)
    if ($r.Status -ne 200) { return }
    $target = Get-CsHeader $r.Headers 'X-CortexSim-Bundle-Target'
    Assert-Cs ($target -eq 'windows') 'X-CortexSim-Bundle-Target is windows' ('got ' + $target)
    $parsed = Get-CsParseErrors -Path $bundle
    Assert-Cs ($parsed.FileErrors.Count -eq 0) ('bundle parses as -File reads it (BOM=' + $parsed.HasBom + ')') (Format-CsParseError $parsed.FileErrors)

    $needsResidue = ([System.IO.File]::ReadAllText($bundle)).Contains('CORTEXSIM_ACCEPT_RESIDUE')
    $createdDir = $false
    if (-not (Test-Path -LiteralPath $script:InstallDir)) {
        New-Item -ItemType Directory -Force -Path $script:InstallDir | Out-Null
        $createdDir = $true
    }
    Remove-CsMarker
    $started = (Get-Date).ToUniversalTime()
    if ($needsResidue) {
        Write-CsInfo 'bundle names CORTEXSIM_ACCEPT_RESIDUE; setting it for this run'
        $env:CORTEXSIM_ACCEPT_RESIDUE = '1'
    }
    try {
        $child = Start-CsChild -FilePath $script:WinPs -Arguments ('-NoProfile -ExecutionPolicy Bypass -File "' + $bundle + '"') -Name 'push'
    } finally {
        Remove-Item -LiteralPath 'env:CORTEXSIM_ACCEPT_RESIDUE' -ErrorAction SilentlyContinue
    }
    $log = $child.Stdout + "`n" + $child.Stderr

    $markerOk = Test-Path -LiteralPath $script:MarkerFile
    Assert-Cs $markerOk 'push step-01 wrote the marker again'
    if ($markerOk) {
        Assert-Cs ((Get-Item -LiteralPath $script:MarkerFile).LastWriteTimeUtc -ge $started.AddSeconds(-2)) 'the marker is from the push run, not the pull run'
    }
    # Positive controls first: without them the negative checks below would
    # pass on a bundle that logged nothing at all.
    Assert-Cs ($log.Contains('Step step-01 completed successfully')) 'push logged step-01 completed successfully'
    Assert-Cs ($log.Contains('Step step-04 completed successfully')) 'push logged step-04 (stderr, exit 0) completed successfully'
    Assert-Cs (-not $log.Contains('Step step-02 completed successfully')) 'push did NOT log step-02 (exit 3) as completed successfully'
    Assert-Cs (-not $log.Contains('Step step-03 completed successfully')) 'push did NOT log step-03 (cmdlet error) as completed successfully'
    Assert-Cs (($null -ne $child.ExitCode) -and ($child.ExitCode -ne 0)) 'bundle exit code is non-zero when steps failed' ('exit=' + $child.ExitCode)

    Remove-CsMarker
    if ($createdDir) { Remove-Item -LiteralPath $script:InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
    foreach ($t in @(Get-ChildItem -LiteralPath $env:TEMP -Filter ('cortexsim-' + $sid + '-*.log') -ErrorAction SilentlyContinue)) {
        Copy-Item -LiteralPath $t.FullName -Destination (Join-Path $script:ProofDir ('push-transcript-' + $t.Name)) -Force
    }
}

function Invoke-PhaseUninstall {
    $svcBefore = Get-Service -Name $script:ServiceName -ErrorAction SilentlyContinue
    $dirBefore = Test-Path -LiteralPath $script:InstallDir
    $procBefore = @(Get-Process -Name 'cortexsim-agent' -ErrorAction SilentlyContinue)
    if ($null -eq $svcBefore -and -not $dirBefore -and $procBefore.Count -eq 0) {
        Assert-Cs $false 'something is installed to uninstall' 'no service, no install dir, no beacon process: an uninstall that removes nothing proves nothing'
        return
    }
    $log = Join-Path $script:InstallDir 'agent.log'
    if (Test-Path -LiteralPath $log) {
        try { Copy-Item -LiteralPath $log -Destination (Join-Path $script:ProofDir 'agent.before-uninstall.log') -Force } catch { Write-CsInfo ('could not copy agent.log: ' + $_.Exception.Message) }
    }
    $oneLiner = 'iwr -useb ''' + $Server + '/api/agents/install?os=windows&uninstall=1'' | iex'
    $child = Start-CsChild -FilePath $script:WinPs -Arguments ('-NoProfile -ExecutionPolicy Bypass -Command "' + $oneLiner + '"') -Name 'uninstall'
    Assert-Cs ($child.ExitCode -eq 0) 'uninstall one-liner exit 0' ('exit=' + $child.ExitCode + ' timed_out=' + $child.TimedOut)

    # SCM deletes a service only once every handle to it closes.
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $svc = Get-Service -Name $script:ServiceName -ErrorAction SilentlyContinue
        $procs = @(Get-Process -Name 'cortexsim-agent' -ErrorAction SilentlyContinue)
        $dir = Test-Path -LiteralPath $script:InstallDir
        if ($null -eq $svc -and $procs.Count -eq 0 -and -not $dir) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    Assert-Cs ($null -eq $svc) ('service ' + $script:ServiceName + ' is gone') ('status=' + $(if ($null -ne $svc) { $svc.Status } else { '' }))
    $left = ''
    if ($dir) { $left = ((@(Get-ChildItem -LiteralPath $script:InstallDir -Recurse -Force -ErrorAction SilentlyContinue) | ForEach-Object { $_.Name }) -join ', ') }
    Assert-Cs (-not $dir) ($script:InstallDir + ' is gone') ('left: ' + $left)
    Assert-Cs ($procs.Count -eq 0) 'no cortexsim-agent process remains' ('pids: ' + ((@($procs) | ForEach-Object { $_.Id }) -join ','))
}

function Invoke-CsCollectStep {
    param([string]$Name, [scriptblock]$Body)
    try { & $Body } catch { Write-CsInfo ('collect ' + $Name + ' failed: ' + $_.Exception.Message) }
}

function Invoke-PhaseCollect {
    $since = (Get-Date).AddHours(-3)
    $jobStart = Get-CsStateValue 'job_started_utc'
    if ($jobStart) { $since = (ConvertTo-CsUtc ([string]$jobStart)).ToLocalTime() }

    Invoke-CsCollectStep 'scm-events' {
        $events = @(Get-CsScmEvents -Since $since)
        $text = (@($events) | Sort-Object TimeCreated | ForEach-Object {
            $_.TimeCreated.ToUniversalTime().ToString('o') + ' ' + $_.Id + ' ' + $_.LevelDisplayName + ' ' + ($_.Message -replace '[\r\n]+', ' ')
        }) -join "`n"
        Write-CsProof -Name 'scm-events.txt' -Text $text
    }
    Invoke-CsCollectStep 'sc.exe' {
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            Write-CsProof -Name 'sc-qc.txt' -Text (& sc.exe qc $script:ServiceName 2>&1 | Out-String)
            Write-CsProof -Name 'sc-queryex.txt' -Text (& sc.exe queryex $script:ServiceName 2>&1 | Out-String)
            Write-CsProof -Name 'sc-qfailure.txt' -Text (& sc.exe qfailure $script:ServiceName 2>&1 | Out-String)
        } finally { $ErrorActionPreference = $prev }
    }
    Invoke-CsCollectStep 'defender' {
        if (Get-Command Get-MpComputerStatus -ErrorAction SilentlyContinue) {
            $mp = Get-MpComputerStatus
            $rec = [ordered]@{
                RealTimeProtectionEnabled = [bool]$mp.RealTimeProtectionEnabled
                AntivirusEnabled          = [bool]$mp.AntivirusEnabled
                AMServiceEnabled          = [bool]$mp.AMServiceEnabled
                BehaviorMonitorEnabled    = [bool]$mp.BehaviorMonitorEnabled
                IsTamperProtected         = [string](Get-CsProp $mp 'IsTamperProtected')
                AMProductVersion          = [string]$mp.AMProductVersion
            }
            Write-CsInfo ('Defender real-time protection: ' + $rec.RealTimeProtectionEnabled)
            Write-CsProof -Name 'defender.json' -Text (ConvertTo-Json -InputObject $rec)
        } else {
            Write-CsProof -Name 'defender.json' -Text '{"available": false}'
        }
    }
    Invoke-CsCollectStep 'simcore-api' {
        foreach ($pair in @(@('/api/agents/install/attempts?limit=100', 'install-attempts.json'), @('/api/agents', 'agents.json'), @('/api/runs', 'runs.json'), @('/api/health', 'health-final.json'))) {
            $r = Invoke-Cs -Path $pair[0] -TimeoutSec 10
            Write-CsProof -Name $pair[1] -Text ('HTTP ' + $r.Status + "`n" + $r.Text + $r.Error)
        }
    }
    Invoke-CsCollectStep 'control-ping' {
        $cp = Get-CsStateValue 'control_ping_pid'
        if ($cp) { Stop-Process -Id ([int]$cp) -Force -ErrorAction SilentlyContinue }
    }
    Invoke-CsCollectStep 'stop-simcore' {
        $simPid = Get-CsStateValue 'simcore_pid'
        if ($simPid) {
            $p = Get-Process -Id ([int]$simPid) -ErrorAction SilentlyContinue
            if ($null -ne $p) {
                # The pid is the cmd.exe wrapper; /T takes uvicorn (its child) too.
                & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID ([int]$simPid) /T /F | Out-Null
                $null = $p.WaitForExit(15000)
            }
            Write-CsInfo ('stopped SimCore pid ' + $simPid)
        }
    }
    Invoke-CsCollectStep 'db' {
        $db = Join-Path $script:Workspace 'data\cortexsim.db'
        if (Test-Path -LiteralPath $db) { Copy-Item -LiteralPath $db -Destination (Join-Path $script:ProofDir 'cortexsim.db') -Force }
    }
    Invoke-CsCollectStep 'agent-log' {
        $log = Join-Path $script:InstallDir 'agent.log'
        if (Test-Path -LiteralPath $log) { Copy-Item -LiteralPath $log -Destination (Join-Path $script:ProofDir 'agent.final.log') -Force }
    }
    Add-CsSummary ''
    Add-CsSummary 'Not covered by this job: https SimCore under 5.1 TLS defaults, Server Core, non-cp1252 code pages, AppLocker/WDAC/CLM, a real XDR agent, reboot survival, the non-admin install path. tenant-verified: 0.'
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

$phases = @{
    'floor'     = { Invoke-PhaseFloor }
    'go'        = { Invoke-PhaseGo }
    'build'     = { Invoke-PhaseBuild }
    'render'    = { Invoke-PhaseRender }
    'parse'     = { Invoke-PhaseParse }
    'pssa'      = { Invoke-PhasePssa }
    'boot'      = { Invoke-PhaseBoot }
    'install'   = { Invoke-PhaseInstall }
    'pull'      = { Invoke-PhasePull }
    'abort'     = { Invoke-PhaseAbort }
    'push'      = { Invoke-PhasePush }
    'uninstall' = { Invoke-PhaseUninstall }
    'collect'   = { Invoke-PhaseCollect }
}

$fatal = $null
try {
    & $phases[$Phase]
} catch {
    $fatal = $_
}

if ($null -ne $fatal) {
    $msg = $fatal.Exception.Message
    Write-Host ('[FAIL] phase ' + $Phase + ' stopped: ' + $msg)
    Write-Host ('::error title=windows ' + $Phase + '::' + (($msg -replace '[\r\n]+', ' ') -replace '::', ': :'))
    Write-Host ($fatal.ScriptStackTrace)
}

if ($Phase -eq 'collect') {
    # Evidence collection never decides the verdict.
    exit 0
}

$count = $script:Failures.Count
if ($null -ne $fatal) { $count = $count + 1 }
if ($count -eq 0) {
    Add-CsSummary ('| ' + $Phase + ' | pass | 0 |')
    Write-CsInfo 'PASS'
    exit 0
}
$first = ''
if ($script:Failures.Count -gt 0) { $first = $script:Failures[0] } elseif ($null -ne $fatal) { $first = $fatal.Exception.Message }
Add-CsSummary ('| ' + $Phase + ' | FAIL | ' + $count + ': ' + ((Limit-Cs $first 160) -replace '\|', '/') + ' |')
Write-CsInfo ('FAIL (' + $count + ')')
exit 1
