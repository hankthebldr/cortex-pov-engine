<#
.SYNOPSIS
    Windows PowerShell 5.1 compatibility gate over a directory of rendered .ps1 files.

.DESCRIPTION
    NECESSARY, NOT SUFFICIENT. Read this before quoting a green run.

    This gate runs under whatever PowerShell invokes it: pwsh 7 on the ubuntu CI
    runner and on a DC laptop. pwsh is NOT Windows PowerShell 5.1, and 5.1 is the
    product floor (Server 2016+ / Windows 10+ ship it in-box; pwsh is not on a
    customer's golden image). A pass here proves three things and no more:

      1. every file DECODES the way 5.1 reads it from disk -- UTF-8 when it
         carries a BOM, otherwise Windows-1252 -- and still parses;
      2. the UTF-8 read of the same bytes (the `iwr | iex` delivery path)
         parses too;
      3. the AST contains none of the PowerShell-7-only constructs listed in
         the rule table below.

    It does NOT prove a single file executes on Windows. A cmdlet that exists
    only in 7, a .NET API missing from .NET Framework 4.x, or a 5.1 behaviour
    difference (native-argument quoting, default encodings) passes this gate
    and fails on the customer host. PSScriptAnalyzer and real execution run in
    the windows-native-ci job, which is the only place 5.1 itself is exercised.

    Input: either a directory with manifest.json (written by
    scripts/ci/render_powershell.py) or a plain directory, in which case every
    *.ps1 below it is checked as kind 'file'. An empty category is DEGRADED
    and fails: "rendered 0 <kind>" is an error, never an ok.

    Output: one line per finding, `<file>:<line>:<RULE>: <message>`, then the
    counts per kind. Exit 1 on any finding or parse error, else 0.

    This file is ASCII and is itself 5.1-parseable; the renderer feeds it back
    through this gate as a tracked *.ps1, so it is checked by its own rules.

.PARAMETER Dir
    The directory to check.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Dir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Off

$ManifestSchema = 'cortexsim.ps-gate.manifest/1'

# ---------------------------------------------------------------------------
# Rule table
# ---------------------------------------------------------------------------

# PS6+ automatic variables. On 5.1 they are undefined, so $null, so FALSE -- an
# `if ($IsWindows)` therefore skips the Windows branch ON Windows. Names only;
# the leading sigil is left off so this file does not trip its own rule.
$Ps7Variables = @('IsWindows', 'IsLinux', 'IsMacOS', 'IsCoreCLR', 'PSStyle')

# Parameters every 5.1 cmdlet accepts. Used only to keep the abbreviation
# check below from flagging a prefix that 5.1 resolves to a common parameter.
$CommonParameters = @(
    'Verbose', 'Debug', 'ErrorAction', 'WarningAction', 'InformationAction',
    'ErrorVariable', 'WarningVariable', 'InformationVariable', 'OutVariable',
    'OutBuffer', 'PipelineVariable', 'WhatIf', 'Confirm', 'UseTransaction'
)

$WebBanned = @(
    'SkipCertificateCheck', 'SslProtocol', 'Form', 'MaximumRetryCount',
    'StatusCodeVariable', 'RetryIntervalSec', 'SkipHeaderValidation',
    'SkipHttpErrorCheck', 'Authentication', 'Token',
    'AllowUnencryptedAuthentication', 'CustomMethod', 'NoProxy',
    'ResponseHeadersVariable', 'Resume', 'PreserveAuthorizationOnRedirect',
    'HttpVersion', 'FollowRelLink', 'MaximumFollowRelLink',
    'ConnectionTimeoutSeconds', 'OperationTimeoutSeconds', 'UnixSocket',
    'AllowInsecureRedirect', 'PreserveHttpMethodOnRedirect'
)
$WebLegal = @(
    'UseBasicParsing', 'Uri', 'WebSession', 'SessionVariable', 'Credential',
    'UseDefaultCredentials', 'CertificateThumbprint', 'Certificate', 'UserAgent',
    'DisableKeepAlive', 'TimeoutSec', 'Headers', 'MaximumRedirection', 'Method',
    'Proxy', 'ProxyCredential', 'ProxyUseDefaultCredentials', 'Body',
    'ContentType', 'TransferEncoding', 'InFile', 'OutFile', 'PassThru'
)
$ContentLegal = @(
    'Path', 'LiteralPath', 'Filter', 'Include', 'Exclude', 'Force', 'Credential',
    'Encoding', 'Stream', 'Value', 'PassThru', 'NoNewline', 'ReadCount',
    'TotalCount', 'Tail', 'Delimiter', 'Wait', 'Raw', 'First', 'Head', 'Last'
)

# Per-cmdlet: parameters that do not exist in 5.1 (Banned) and the ones that do
# (Legal, used only to disambiguate abbreviations).
$CommandRules = @{
    'ConvertFrom-Json'  = @{ Banned = @('AsHashtable', 'Depth', 'NoEnumerate', 'DateKind'); Legal = @('InputObject') }
    'ConvertTo-Json'    = @{ Banned = @('AsArray', 'EnumsAsStrings', 'EscapeHandling'); Legal = @('InputObject', 'Depth', 'Compress') }
    'Invoke-WebRequest' = @{ Banned = $WebBanned; Legal = $WebLegal }
    'Invoke-RestMethod' = @{ Banned = $WebBanned; Legal = $WebLegal }
    'Get-Content'       = @{ Banned = @('AsByteStream'); Legal = $ContentLegal }
    'Set-Content'       = @{ Banned = @('AsByteStream'); Legal = $ContentLegal }
    'Add-Content'       = @{ Banned = @('AsByteStream'); Legal = $ContentLegal }
    'ForEach-Object'    = @{ Banned = @('Parallel', 'ThrottleLimit', 'TimeoutSeconds', 'AsJob', 'UseNewRunspace'); Legal = @('InputObject', 'Begin', 'Process', 'End', 'RemainingScripts', 'MemberName', 'ArgumentList') }
    'Join-Path'         = @{ Banned = @('AdditionalChildPath'); Legal = @('Path', 'ChildPath', 'Resolve', 'Credential') }
    'Split-Path'        = @{ Banned = @('LeafBase', 'Extension'); Legal = @('Path', 'LiteralPath', 'Parent', 'Leaf', 'Qualifier', 'NoQualifier', 'IsAbsolute', 'Resolve', 'Credential') }
}

# 5.1 aliases (curl/wget ARE Invoke-WebRequest on 5.1; sc IS Set-Content).
$CommandAliases = @{
    'iwr' = 'Invoke-WebRequest'; 'curl' = 'Invoke-WebRequest'; 'wget' = 'Invoke-WebRequest'
    'irm' = 'Invoke-RestMethod'
    'gc' = 'Get-Content'; 'cat' = 'Get-Content'; 'type' = 'Get-Content'
    'sc' = 'Set-Content'; 'ac' = 'Add-Content'
    '%' = 'ForEach-Object'; 'foreach' = 'ForEach-Object'
}

# -Encoding values PowerShell 6+ added. 5.1's enums reject them.
$Ps7EncodingValues = @('utf8NoBOM', 'utf8BOM', 'ansi')

# Switch parameters of Join-Path (for counting its positional children).
$JoinPathSwitches = @('Resolve', 'Verbose', 'Debug')

# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

$script:Findings = New-Object System.Collections.Generic.List[object]
$script:Seen = @{}
$script:Warnings = 0

function Add-Finding {
    param([string]$File, [int]$Line, [string]$Rule, [string]$Message)
    $key = '{0}|{1}|{2}|{3}' -f $File, $Line, $Rule, $Message
    if ($script:Seen.ContainsKey($key)) { return }
    $script:Seen[$key] = $true
    $script:Findings.Add((New-Object PSObject -Property @{ File = $File; Line = $Line; Rule = $Rule; Message = $Message }))
    Write-Output ('{0}:{1}:{2}: {3}' -f $File, $Line, $Rule, $Message)
}

function Write-GateWarning {
    param([string]$Message)
    $script:Warnings++
    Write-Output ('[ps51-gate] WARN: ' + $Message)
}

# ---------------------------------------------------------------------------
# Decoding: the way Windows PowerShell 5.1 reads a script file
# ---------------------------------------------------------------------------

# Windows-1252 0x80..0x9F. The rest of the code page is Latin-1. Used only when
# this runtime cannot hand out code page 1252 itself.
$Cp1252High = @(
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
    0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
    0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
    0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178
)
$script:Cp1252 = $null
try {
    $script:Cp1252 = [System.Text.Encoding]::GetEncoding(1252)
} catch {
    $script:Cp1252 = $null
}

function ConvertFrom-Cp1252 {
    param([byte[]]$Bytes)
    if ($null -ne $script:Cp1252) { return $script:Cp1252.GetString($Bytes) }
    $sb = New-Object System.Text.StringBuilder ($Bytes.Length)
    foreach ($b in $Bytes) {
        if ($b -ge 0x80 -and $b -le 0x9F) {
            [void]$sb.Append([char]$Cp1252High[$b - 0x80])
        } else {
            [void]$sb.Append([char]$b)
        }
    }
    return $sb.ToString()
}

function Get-Decoding {
    # Returns @{ Reads = list of @{ Rule; Label; Text }; Utf8Valid; Bom }
    param([byte[]]$Bytes)
    $reads = New-Object System.Collections.Generic.List[object]
    $n = $Bytes.Length
    if ($n -ge 3 -and $Bytes[0] -eq 0xEF -and $Bytes[1] -eq 0xBB -and $Bytes[2] -eq 0xBF) {
        $text = (New-Object System.Text.UTF8Encoding($false)).GetString($Bytes, 3, $n - 3)
        $reads.Add(@{ Rule = 'PARSE_BOM'; Label = 'UTF-8 with BOM (how 5.1 and iwr both read it)'; Text = $text })
        return @{ Reads = $reads; Utf8Valid = $true; Bom = 'utf-8' }
    }
    if ($n -ge 2 -and $Bytes[0] -eq 0xFF -and $Bytes[1] -eq 0xFE) {
        $reads.Add(@{ Rule = 'PARSE_BOM'; Label = 'UTF-16LE with BOM'; Text = [System.Text.Encoding]::Unicode.GetString($Bytes, 2, $n - 2) })
        return @{ Reads = $reads; Utf8Valid = $true; Bom = 'utf-16le' }
    }
    if ($n -ge 2 -and $Bytes[0] -eq 0xFE -and $Bytes[1] -eq 0xFF) {
        $reads.Add(@{ Rule = 'PARSE_BOM'; Label = 'UTF-16BE with BOM'; Text = [System.Text.Encoding]::BigEndianUnicode.GetString($Bytes, 2, $n - 2) })
        return @{ Reads = $reads; Utf8Valid = $true; Bom = 'utf-16be' }
    }

    $ansi = ConvertFrom-Cp1252 $Bytes
    $utf8 = $null
    $valid = $true
    try {
        $utf8 = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($Bytes)
    } catch {
        $valid = $false
    }
    if ($valid -and $utf8 -ceq $ansi) {
        # Pure ASCII: both reads are the same text, parse it once.
        $reads.Add(@{ Rule = 'PARSE'; Label = 'ASCII (5.1 and UTF-8 reads identical)'; Text = $ansi })
    } else {
        $reads.Add(@{ Rule = 'PARSE_CP1252'; Label = 'Windows-1252, BOM-less (how 5.1 reads it from disk)'; Text = $ansi })
        if ($valid) {
            $reads.Add(@{ Rule = 'PARSE_UTF8'; Label = 'UTF-8 (the iwr | iex path)'; Text = $utf8 })
        }
    }
    return @{ Reads = $reads; Utf8Valid = $valid; Bom = $null }
}

function Invoke-Parse {
    param([string]$Text)
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)
    return @{ Ast = $ast; Tokens = $tokens; Errors = $errors }
}

# ---------------------------------------------------------------------------
# AST walk
# ---------------------------------------------------------------------------

function Get-BareVariableName {
    param($VariableAst)
    $p = $VariableAst.VariablePath.UserPath
    if ($p -match '^(?i:global|script|local|private):(.+)$') { return $Matches[1] }
    return $p
}

function Test-AllowlistedPlatformCheck {
    # The ONE allowed shape: `if ($IsLinux -or $IsMacOS)` -- exactly those two
    # unqualified variables, in that order, as the whole condition of an if or
    # elseif clause. That is the push harness's off-Windows dry-run branch,
    # which is correct on 5.1 because both are $null there. Anything wider
    # (the variables wholesale, -and, a third operand, a negation, a while)
    # is a finding: tolerance hides bugs.
    param($VariableAst)
    $bin = $VariableAst.Parent
    if ($null -eq $bin -or $bin.GetType().Name -ne 'BinaryExpressionAst') { return $false }
    if ($bin.Operator.ToString() -ne 'Or') { return $false }
    $l = $bin.Left
    $r = $bin.Right
    if ($l.GetType().Name -ne 'VariableExpressionAst' -or $r.GetType().Name -ne 'VariableExpressionAst') { return $false }
    if ($l.Splatted -or $r.Splatted) { return $false }
    if ($l.VariablePath.UserPath -ne 'IsLinux' -or $r.VariablePath.UserPath -ne 'IsMacOS') { return $false }
    $ce = $bin.Parent
    if ($null -eq $ce -or $ce.GetType().Name -ne 'CommandExpressionAst') { return $false }
    if ($ce.Redirections.Count -gt 0) { return $false }
    $pl = $ce.Parent
    if ($null -eq $pl -or $pl.GetType().Name -ne 'PipelineAst' -or $pl.PipelineElements.Count -ne 1) { return $false }
    $ifAst = $pl.Parent
    if ($null -eq $ifAst -or $ifAst.GetType().Name -ne 'IfStatementAst') { return $false }
    foreach ($clause in $ifAst.Clauses) {
        if ([object]::ReferenceEquals($clause.Item1, $pl)) { return $true }
    }
    return $false
}

function Test-ParameterMatch {
    # True when $Used names $Banned on PowerShell 7: an exact match, or an
    # abbreviation (3+ characters) that 5.1 could not resolve to a legal
    # parameter of the same cmdlet.
    param([string]$Used, [string]$Banned, [string[]]$Legal)
    if ($Used -ieq $Banned) { return $true }
    if ($Used.Length -lt 3) { return $false }
    if (-not $Banned.StartsWith($Used, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    foreach ($l in @($Legal) + $CommonParameters) {
        if ($l.StartsWith($Used, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    return $true
}

function Get-CommandArguments {
    # Pair each -Parameter with its value (colon form or the next element) and
    # collect the positional arguments. $Switches lists the parameters known to
    # take no value, so their neighbour is counted as positional.
    param($CommandAst, [string[]]$Switches)
    $named = New-Object System.Collections.Generic.List[object]
    $positional = New-Object System.Collections.Generic.List[object]
    $splat = $false
    $els = $CommandAst.CommandElements
    $i = 1
    while ($i -lt $els.Count) {
        $e = $els[$i]
        if ($e -is [System.Management.Automation.Language.CommandParameterAst]) {
            $value = $e.Argument
            $isSwitch = $false
            foreach ($s in @($Switches)) {
                if ($s.StartsWith($e.ParameterName, [System.StringComparison]::OrdinalIgnoreCase)) { $isSwitch = $true }
            }
            if ($null -eq $value -and -not $isSwitch -and ($i + 1) -lt $els.Count -and
                -not ($els[$i + 1] -is [System.Management.Automation.Language.CommandParameterAst])) {
                $value = $els[$i + 1]
                $i++
            }
            $named.Add(@{ Name = $e.ParameterName; Ast = $e; Value = $value })
        } else {
            if ($e -is [System.Management.Automation.Language.VariableExpressionAst] -and $e.Splatted) { $splat = $true }
            $positional.Add($e)
        }
        $i++
    }
    return @{ Named = $named; Positional = $positional; Splat = $splat }
}

function Test-Command {
    param($CommandAst, [string]$Rel)
    $raw = $CommandAst.GetCommandName()
    if ([string]::IsNullOrEmpty($raw)) { return }
    $name = ($raw -split '\\')[-1]
    $canonical = $name
    if ($CommandAliases.ContainsKey($name)) { $canonical = $CommandAliases[$name] }
    $line = $CommandAst.Extent.StartLineNumber
    $cmdletShaped = ($canonical -like '*-*') -or $CommandAliases.ContainsKey($name)
    if (-not $cmdletShaped) { return }

    $switches = @()
    if ($canonical -ieq 'Join-Path') { $switches = $JoinPathSwitches }
    $parsed = Get-CommandArguments $CommandAst $switches

    if ($CommandRules.ContainsKey($canonical)) {
        $rule = $CommandRules[$canonical]
        foreach ($p in $parsed.Named) {
            foreach ($banned in $rule.Banned) {
                if (Test-ParameterMatch $p.Name $banned $rule.Legal) {
                    Add-Finding $Rel $line 'PS7_PARAMETER' ('{0} -{1} is PowerShell 6+ (Windows PowerShell 5.1 has no -{2})' -f $canonical, $p.Name, $banned)
                }
            }
        }
    }

    if ($canonical -ieq 'Join-Path' -and -not $parsed.Splat) {
        $children = $parsed.Positional.Count
        foreach ($p in $parsed.Named) {
            foreach ($pathParam in @('Path', 'ChildPath')) {
                if ($pathParam.StartsWith($p.Name, [System.StringComparison]::OrdinalIgnoreCase) -and $p.Name.Length -ge 1) { $children++ }
            }
        }
        if ($children -gt 2) {
            Add-Finding $Rel $line 'PS7_JOIN_PATH_CHILDREN' ('Join-Path with {0} path arguments: 5.1 takes exactly -Path and -ChildPath (more is PowerShell 6+ -AdditionalChildPath)' -f $children)
        }
    }

    foreach ($p in $parsed.Named) {
        if ($p.Name.Length -ge 3 -and 'Encoding'.StartsWith($p.Name, [System.StringComparison]::OrdinalIgnoreCase)) {
            $v = $p.Value
            if ($null -ne $v -and $v.GetType().Name -eq 'StringConstantExpressionAst') {
                foreach ($bad in $Ps7EncodingValues) {
                    if ($v.Value -ieq $bad) {
                        Add-Finding $Rel $line 'PS7_ENCODING_VALUE' ('{0} -Encoding {1} is PowerShell 6+ (5.1 rejects it)' -f $canonical, $v.Value)
                    }
                }
            }
        }
    }
}

function Test-Ast {
    param($Parse, [string]$Rel)
    $nodes = $Parse.Ast.FindAll({ param($n) $true }, $true)
    foreach ($n in $nodes) {
        $type = $n.GetType().Name
        $line = $n.Extent.StartLineNumber
        switch ($type) {
            'PipelineChainAst' {
                Add-Finding $Rel $line 'PS7_PIPELINE_CHAIN' 'pipeline chain operators && and || are PowerShell 7+'
            }
            'TernaryExpressionAst' {
                Add-Finding $Rel $line 'PS7_TERNARY' 'the ternary operator a ? b : c is PowerShell 7+'
            }
            'BinaryExpressionAst' {
                if ($n.Operator.ToString() -eq 'QuestionQuestion') {
                    Add-Finding $Rel $line 'PS7_NULL_COALESCE' 'the null-coalescing operator ?? is PowerShell 7+'
                }
            }
            'AssignmentStatementAst' {
                if ($n.Operator.ToString() -eq 'QuestionQuestionEquals') {
                    Add-Finding $Rel $line 'PS7_NULL_COALESCE_ASSIGN' 'the null-coalescing assignment ??= is PowerShell 7+'
                }
            }
            'MemberExpressionAst' {
                if ($n.NullConditional) { Add-Finding $Rel $line 'PS7_NULL_CONDITIONAL' 'null-conditional member access ?. is PowerShell 7+' }
            }
            'InvokeMemberExpressionAst' {
                if ($n.NullConditional) { Add-Finding $Rel $line 'PS7_NULL_CONDITIONAL' 'null-conditional member access ?. is PowerShell 7+' }
            }
            'IndexExpressionAst' {
                if ($n.NullConditional) { Add-Finding $Rel $line 'PS7_NULL_CONDITIONAL' 'null-conditional index ?[] is PowerShell 7+' }
            }
            'NamedBlockAst' {
                if ($n.BlockKind.ToString() -eq 'Clean') {
                    Add-Finding $Rel $line 'PS7_CLEAN_BLOCK' 'the clean {} block is PowerShell 7.3+'
                }
            }
            'PipelineAst' {
                if ($n.Background) {
                    Add-Finding $Rel $line 'PS7_BACKGROUND' 'a trailing & (background job) is PowerShell 6+'
                }
            }
            'VariableExpressionAst' {
                $bare = Get-BareVariableName $n
                if ($Ps7Variables -contains $bare) {
                    if (-not (Test-AllowlistedPlatformCheck $n)) {
                        Add-Finding $Rel $line 'PS7_AUTOMATIC_VARIABLE' ('{0} is a PowerShell 6+ automatic variable: on 5.1 it is $null (false). Only the exact shape if (IsLinux -or IsMacOS) is allowed' -f ('$' + $bare))
                    }
                }
            }
            'CommandAst' {
                Test-Command $n $Rel
            }
        }
    }
    foreach ($tok in $Parse.Tokens) {
        $kind = $tok.Kind.ToString()
        if (($kind -eq 'StringExpandable' -or $kind -eq 'HereStringExpandable') -and $tok.Text -match '`u\{') {
            Add-Finding $Rel $tok.Extent.StartLineNumber 'PS7_UNICODE_ESCAPE' 'the `u{XXXX} escape is PowerShell 6+ (5.1 emits a literal u{XXXX})'
        }
    }
}

# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

function Test-File {
    param([string]$Full, [string]$Rel)
    $bytes = [System.IO.File]::ReadAllBytes($Full)
    $dec = Get-Decoding $bytes
    if (-not $dec.Utf8Valid) {
        Add-Finding $Rel 1 'INVALID_UTF8' 'bytes are not valid UTF-8; the iwr | iex path would decode them to replacement characters'
    }
    $walk = $null
    foreach ($read in $dec.Reads) {
        $parse = Invoke-Parse $read.Text
        foreach ($err in $parse.Errors) {
            Add-Finding $Rel $err.Extent.StartLineNumber $read.Rule ('[{0}] {1}: {2}' -f $read.Label, $err.ErrorId, $err.Message)
        }
        # Walk the intended text: the UTF-8 read when there is one, else the
        # only read. A file that fails the cp1252 parse is already a finding.
        if ($read.Rule -ne 'PARSE_CP1252' -or $null -eq $walk) { $walk = $parse }
    }
    if ($null -ne $walk) { Test-Ast $walk $Rel }

    if ($null -eq $dec.Bom -and $dec.Utf8Valid -and $dec.Reads.Count -gt 1) {
        $nonAscii = 0
        foreach ($b in $bytes) { if ($b -ge 0x80) { $nonAscii++ } }
        Write-GateWarning ('{0}: {1} non-ASCII byte(s) in a BOM-less file -- 5.1 reads them as Windows-1252, so any text they carry is mojibake on Windows' -f $Rel, $nonAscii)
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

$edition = 'Desktop'
if ($PSVersionTable.ContainsKey('PSEdition')) { $edition = [string]$PSVersionTable.PSEdition }
$hostVersion = $PSVersionTable.PSVersion.ToString()
if ($edition -eq 'Desktop') {
    Write-Output ('[ps51-gate] host: Windows PowerShell {0} (Desktop) -- the real 5.1 parser' -f $hostVersion)
} else {
    Write-Output ('[ps51-gate] host: PowerShell {0} ({1}) -- NOT Windows PowerShell 5.1; a pass is necessary, not sufficient' -f $hostVersion, $edition)
}
if ($null -eq $script:Cp1252) {
    Write-Output '[ps51-gate] note: code page 1252 unavailable in this runtime; using the built-in Windows-1252 table'
}

if (-not (Test-Path -LiteralPath $Dir -PathType Container)) {
    Write-Output ('[ps51-gate] FAIL: not a directory: {0}' -f $Dir)
    exit 1
}
$root = (Resolve-Path -LiteralPath $Dir).ProviderPath.TrimEnd('\', '/')
$manifestPath = Join-Path $root 'manifest.json'

$entries = New-Object System.Collections.Generic.List[object]
$requiredKinds = @()
if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
    $manifest = [System.IO.File]::ReadAllText($manifestPath, (New-Object System.Text.UTF8Encoding($false))) | ConvertFrom-Json
    if ($manifest.schema -ne $ManifestSchema) {
        Write-Output ('manifest.json:1:MANIFEST_SCHEMA: unrecognised manifest schema {0} (expected {1}) -- refusing to read it as empty' -f $manifest.schema, $ManifestSchema)
        exit 1
    }
    foreach ($e in @($manifest.errors)) {
        if ($null -ne $e -and "$e" -ne '') { Add-Finding 'manifest.json' 1 'RENDER_ERROR' ([string]$e) }
    }
    foreach ($w in @($manifest.warnings)) {
        if ($null -ne $w -and "$w" -ne '') { Write-GateWarning ('renderer: ' + [string]$w) }
    }
    $requiredKinds = @($manifest.required_kinds | Where-Object { $null -ne $_ })
    foreach ($a in @($manifest.artifacts)) {
        if ($null -eq $a) { continue }
        $entries.Add(@{ Path = [string]$a.path; Kind = [string]$a.kind; NotPowerShell = $a.not_powershell })
    }
} else {
    foreach ($f in @(Get-ChildItem -LiteralPath $root -Recurse -File -Filter '*.ps1')) {
        $rel = $f.FullName.Substring($root.Length).TrimStart('\', '/')
        $entries.Add(@{ Path = $rel; Kind = 'file'; NotPowerShell = $null })
    }
    $requiredKinds = @('file')
}

$perKindFiles = @{}
$perKindFailing = @{}
foreach ($k in $requiredKinds) { $perKindFiles[$k] = 0; $perKindFailing[$k] = 0 }

foreach ($entry in $entries) {
    $kind = $entry.Kind
    if (-not $perKindFiles.ContainsKey($kind)) { $perKindFiles[$kind] = 0; $perKindFailing[$kind] = 0 }
    $perKindFiles[$kind]++
    $before = $script:Findings.Count
    $rel = $entry.Path -replace '\\', '/'
    $full = Join-Path $root $entry.Path
    if ($null -ne $entry.NotPowerShell -and "$($entry.NotPowerShell)" -ne '') {
        Add-Finding $rel 1 'NOT_POWERSHELL' ([string]$entry.NotPowerShell)
    } elseif (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        Add-Finding $rel 1 'MISSING_FILE' 'listed in manifest.json but not on disk'
    } else {
        Test-File $full $rel
    }
    if ($script:Findings.Count -gt $before) { $perKindFailing[$kind]++ }
}

foreach ($k in $requiredKinds) {
    if ($perKindFiles[$k] -eq 0) {
        Add-Finding 'manifest.json' 1 'DEGRADED_EMPTY' ('rendered 0 {0} -- an empty category is degraded, not ok' -f $k)
    }
}

Write-Output ''
foreach ($k in @($perKindFiles.Keys | Sort-Object)) {
    Write-Output ('[ps51-gate] kind={0,-15} files={1,-5} failing={2}' -f $k, $perKindFiles[$k], $perKindFailing[$k])
}
$total = 0
foreach ($k in $perKindFiles.Keys) { $total += $perKindFiles[$k] }
if ($script:Findings.Count -gt 0) {
    Write-Output ('[ps51-gate] FAIL: {0} finding(s) across {1} file(s); {2} warning(s)' -f $script:Findings.Count, $total, $script:Warnings)
    exit 1
}
Write-Output ('[ps51-gate] OK: {0} file(s), 0 findings, {1} warning(s) -- pwsh parse + 5.1 decode + PS7-construct denylist only; not proof of execution on Windows' -f $total, $script:Warnings)
exit 0
