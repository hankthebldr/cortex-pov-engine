<#
.SYNOPSIS
  Throwaway spike for the POVengine jumpbox installer (risks R1 and R2).
  Not product code. The deliverable is the recorded result, not this script.

.DESCRIPTION
  R1: which account can keep a WSL2 distribution running unattended, across a
  reboot, from a Windows service (or, failing that, a sign-in task)?
  R2: can a GitHub-hosted Windows runner run WSL2 plus dockerd at all?

  Modes
    probe    R2. Import the Alpine mini rootfs as povengine-spike, install
             docker inside it, start dockerd, run docker info and hello-world.
             No service, no account, no task. This is what CI runs.
    system   R1(a). A service running as LocalSystem keeps the distribution up.
    account  R1(b). A service running as the local account povengine-spike-svc.
    logon    R1(c). A scheduled task that starts at sign-in of this account.
    cleanup  Remove the distribution, service, account, task, portproxy rule
             and firewall rule, whichever exist.

  Every mode writes one JSON object (see -ResultPath). The JSON holds booleans,
  versions and sanitised short details only: no host names, no addresses.

  Runs under Windows PowerShell 5.1. Run from an elevated prompt.

.PARAMETER PostReboot
  With system|account|logon: skip all setup and only observe what survived a
  reboot. Writes spike-result-postreboot.json by default.

.PARAMETER ProvisionOnly
  Internal. Import the distribution and install docker, then stop. Used to
  provision the distribution under another account through a one-shot task.

.PARAMETER LocalDistroOnly
  Internal. With -Mode cleanup: remove only this account's distribution.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('system', 'account', 'logon', 'probe', 'cleanup')]
    [string]$Mode,
    [string]$ResultPath = '',
    [int]$IdleWaitSeconds = 120,
    [switch]$PostReboot,
    [switch]$ProvisionOnly,
    [switch]$LocalDistroOnly
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

# ---- constants --------------------------------------------------------------
$script:Distro = 'povengine-spike'
$script:SvcName = 'povengine-spike'
$script:AccountName = 'povengine-spike-svc'
$script:TaskName = 'povengine-spike-logon'
$script:FwRule = 'povengine-spike-18888'
$script:HttpPort = 18888
$script:WorkDir = Join-Path $env:ProgramData 'povengine-spike'
$script:RootfsUrl = 'https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-minirootfs-3.24.4-x86_64.tar.gz'
$script:RootfsSha256 = 'cdaa38cb8de3537967ee5b6c9b6cd00cc962da2b018e6eea48696bb274923012'
$script:RootfsFile = Join-Path $script:WorkDir 'alpine-minirootfs-3.24.4-x86_64.tar.gz'
$script:SvcExe = Join-Path $script:WorkDir 'spike-svc.exe'
$script:SvcLog = Join-Path $script:WorkDir 'service-log.txt'
$script:Checks = New-Object System.Collections.ArrayList
$script:Meta = [ordered]@{}

if (-not $ResultPath) {
    if ($Mode -eq 'cleanup') { $ResultPath = 'spike-cleanup.json' }
    elseif ($PostReboot) { $ResultPath = 'spike-result-postreboot.json' }
    else { $ResultPath = 'spike-result.json' }
}
if (-not [IO.Path]::IsPathRooted($ResultPath)) {
    $ResultPath = Join-Path (Get-Location).Path $ResultPath
}

# ---- helpers ----------------------------------------------------------------
function Get-Clean {
    # Keep details short and free of this machine's name and any IPv4 address.
    # -KeepVersions is for text that is known to carry version numbers only
    # (wsl --version). WSL and kernel versions are four-part dotted numbers, so
    # the address scrub must not run on them.
    param([string]$Text, [switch]$KeepVersions)
    if (-not $Text) { return '' }
    $t = $Text -replace "[\r\n]+", ' '
    if (-not $KeepVersions) { $t = $t -replace '\b\d{1,3}(\.\d{1,3}){3}\b', '<addr>' }
    if ($env:COMPUTERNAME) { $t = $t -replace [regex]::Escape($env:COMPUTERNAME), '<host>' }
    if ($env:USERNAME) { $t = $t -replace [regex]::Escape($env:USERNAME), '<user>' }
    $t = $t.Trim()
    $cap = 240
    if ($KeepVersions) { $cap = 800 }
    if ($t.Length -gt $cap) { $t = $t.Substring(0, $cap) }
    return $t
}

function Add-Check {
    param([string]$Check, [bool]$Ok, [string]$Detail = '', [switch]$KeepVersions)
    $d = Get-Clean $Detail -KeepVersions:$KeepVersions
    [void]$script:Checks.Add([ordered]@{ check = $Check; ok = $Ok; detail = $d })
    $mark = 'FAIL'
    if ($Ok) { $mark = 'ok  ' }
    Write-Host ('[' + $mark + '] ' + $Check + ' ' + $d)
}

function Write-Result {
    param([bool]$Ok)
    $obj = [ordered]@{
        mode       = $Mode
        post_reboot = [bool]$PostReboot
        ok         = $Ok
        windows    = $script:Meta
        checks     = @($script:Checks.ToArray())
    }
    $json = ConvertTo-Json -InputObject $obj -Depth 6
    [IO.File]::WriteAllText($ResultPath, $json, (New-Object Text.UTF8Encoding $false))
    Write-Host ('result written to ' + $ResultPath)
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal $id
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ---- protected work directory ------------------------------------------------
# SYSTEM tasks and a LocalSystem service run files from this directory, so it
# must never be writable by anyone but SYSTEM and Administrators. It is created
# with a protected ACL in the same call that creates it, and an existing
# directory is refused unless its owner, its ACL and its contents check out.
$script:StagedScript = Join-Path $script:WorkDir 'wsl-service-spike.ps1'

function Initialize-WorkDir {
    $path = $script:WorkDir
    $trusted = @('S-1-5-18', 'S-1-5-32-544')
    if (-not (Test-Path -LiteralPath $path)) {
        $sec = New-Object Security.AccessControl.DirectorySecurity
        $sec.SetOwner((New-Object Security.Principal.SecurityIdentifier 'S-1-5-32-544'))
        $sec.SetAccessRuleProtection($true, $false)
        $inh = [Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
        foreach ($t in $trusted) {
            $id = New-Object Security.Principal.SecurityIdentifier $t
            $rule = New-Object Security.AccessControl.FileSystemAccessRule($id, [Security.AccessControl.FileSystemRights]::FullControl, $inh, [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
            $sec.AddAccessRule($rule)
        }
        [void][IO.Directory]::CreateDirectory($path, $sec)
    }
    # Verify whatever is there now, including a directory somebody created first.
    $item = Get-Item -LiteralPath $path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ('refusing ' + $path + ': it is a reparse point') }
    $mask = [int][Security.AccessControl.FileSystemRights]'WriteData,AppendData,WriteExtendedAttributes,WriteAttributes,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
    $all = @($item)
    foreach ($c in @(Get-ChildItem -LiteralPath $path -Force -Recurse)) { $all += $c }
    foreach ($e in $all) {
        if ($e.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ('refusing ' + $e.FullName + ': it is a reparse point') }
        $acl = Get-Acl -LiteralPath $e.FullName
        $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
        if ($trusted -notcontains $owner) { throw ('refusing ' + $e.FullName + ': owner is not SYSTEM or Administrators; inspect and delete it by hand') }
        if ($e.PSIsContainer -and $e.FullName -eq $item.FullName) {
            foreach ($ace in $acl.Access) {
                if ($ace.AccessControlType -ne 'Allow') { continue }
                $sid = $ace.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
                if (($trusted -notcontains $sid) -and (([int]$ace.FileSystemRights -band $mask) -ne 0)) {
                    throw ('refusing ' + $path + ': another principal can write to it; inspect and delete it by hand')
                }
            }
        }
    }
}

function New-StagedFile {
    # An empty file that inherits the protected ACL (an existing one is emptied).
    param([string]$Path)
    [IO.File]::WriteAllBytes($Path, (New-Object byte[] 0))
}

function Grant-FileAccess {
    # Narrow grant to the spike account on ONE path. 'RX' on the work directory
    # is this-folder-only (no inheritance), so the account can traverse it but
    # not create or rename anything in it.
    param([string]$Path, [string]$Perm)
    [void](& icacls.exe $Path /grant ($script:AccountName + ':(' + $Perm + ')'))
    return ($LASTEXITCODE -eq 0)
}

function ConvertTo-ArgText {
    param([string]$Arg)
    if ($Arg -eq '') { return '""' }
    if ($Arg -notmatch '[\s"]') { return $Arg }
    return '"' + ($Arg -replace '"', '\"') + '"'
}

function Convert-Bytes {
    # wsl.exe writes its own messages as UTF-16LE; a command run inside the
    # distribution writes UTF-8. Decode explicitly, never through the console.
    param([byte[]]$Bytes, [string]$Encoding)
    if ($null -eq $Bytes -or $Bytes.Length -eq 0) { return '' }
    $enc = $Encoding
    if ($enc -eq 'auto') {
        $enc = 'utf8'
        $n = [Math]::Min($Bytes.Length, 64)
        for ($i = 0; $i -lt $n; $i++) { if ($Bytes[$i] -eq 0) { $enc = 'utf16'; break } }
    }
    if ($enc -eq 'utf16') { $s = [Text.Encoding]::Unicode.GetString($Bytes) }
    else { $s = (New-Object Text.UTF8Encoding $false).GetString($Bytes) }
    return $s.Replace([string][char]0xFEFF, '').Replace([string][char]0, '')
}

function Invoke-Native {
    param(
        [string]$File,
        [string[]]$ArgList = @(),
        [string]$Encoding = 'auto',
        [int]$TimeoutSec = 300,
        [string]$StdinText = $null
    )
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $parts = @()
    foreach ($a in $ArgList) { $parts += (ConvertTo-ArgText $a) }
    $psi.Arguments = ($parts -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $p = [Diagnostics.Process]::Start($psi)
    $so = New-Object IO.MemoryStream
    $se = New-Object IO.MemoryStream
    $t1 = $p.StandardOutput.BaseStream.CopyToAsync($so)
    $t2 = $p.StandardError.BaseStream.CopyToAsync($se)
    if ($null -ne $StdinText) {
        $b = (New-Object Text.UTF8Encoding $false).GetBytes($StdinText)
        $p.StandardInput.BaseStream.Write($b, 0, $b.Length)
    }
    $p.StandardInput.Close()
    $timedOut = $false
    if (-not $p.WaitForExit($TimeoutSec * 1000)) {
        $timedOut = $true
        try { $p.Kill() } catch { Write-Verbose 'kill failed' }
    }
    $p.WaitForExit()
    [void]$t1.Wait(5000)
    [void]$t2.Wait(5000)
    $text = (Convert-Bytes $so.ToArray() $Encoding) + (Convert-Bytes $se.ToArray() $Encoding)
    $code = -1
    if (-not $timedOut) { $code = $p.ExitCode }
    return [pscustomobject]@{ ExitCode = $code; Output = $text; TimedOut = $timedOut }
}

function Invoke-WslHost {
    # wsl.exe's own commands (--version, --update, --import, ...): UTF-16LE.
    param([string[]]$ArgList, [int]$TimeoutSec = 300)
    return Invoke-Native -File (Join-Path $env:SystemRoot 'System32\wsl.exe') -ArgList $ArgList -Encoding 'utf16' -TimeoutSec $TimeoutSec
}

function Invoke-WslIn {
    # A command run inside the distribution as root, with no shell in between.
    # wsl --exec is not a login shell: /sbin and /usr/sbin are not on PATH, so
    # callers pass absolute program paths.
    param([string[]]$ArgList, [int]$TimeoutSec = 300, [string]$StdinText = $null)
    $all = @('-d', $script:Distro, '-u', 'root', '--exec') + $ArgList
    return Invoke-Native -File (Join-Path $env:SystemRoot 'System32\wsl.exe') -ArgList $all -Encoding 'auto' -TimeoutSec $TimeoutSec -StdinText $StdinText
}

function Get-WinInfo {
    $v = [Environment]::OSVersion.Version
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $script:Meta['build'] = ('{0}.{1}.{2}' -f $v.Major, $v.Minor, $v.Build)
    $script:Meta['edition'] = [string]$os.Caption
    $script:Meta['hypervisor_present'] = [bool]$cs.HypervisorPresent
    $script:Meta['build_number'] = [int]$v.Build
    $w = Invoke-WslHost -ArgList @('--version') -TimeoutSec 60
    $script:Meta['wsl_version_text'] = (Get-Clean ($w.Output -replace '\s{2,}', ' ') -KeepVersions)
    $script:Meta['wsl_version_exit'] = $w.ExitCode
    return $w
}

function Initialize-WslHost {
    # Features, then wsl --update (older inbox WSL has no --update: tolerate it).
    foreach ($f in @('VirtualMachinePlatform', 'Microsoft-Windows-Subsystem-Linux')) {
        try {
            $st = Get-WindowsOptionalFeature -Online -FeatureName $f
            if ($st.State -ne 'Enabled') {
                $r = Enable-WindowsOptionalFeature -Online -FeatureName $f -NoRestart -All
                Add-Check ('feature ' + $f) $true ('was ' + $st.State + ', enabled, restart needed: ' + $r.RestartNeeded)
            }
            else { Add-Check ('feature ' + $f) $true 'already enabled' }
        }
        catch { Add-Check ('feature ' + $f) $false $_.Exception.Message }
    }
    $u = Invoke-WslHost -ArgList @('--update') -TimeoutSec 600
    Add-Check 'wsl --update' ($u.ExitCode -eq 0) ('exit ' + $u.ExitCode + ' ' + $u.Output + ' (failure here is tolerated)')
    $d = Invoke-WslHost -ArgList @('--set-default-version', '2') -TimeoutSec 60
    Add-Check 'wsl --set-default-version 2' ($d.ExitCode -eq 0) ('exit ' + $d.ExitCode + ' ' + $d.Output)
    $w = Get-WinInfo
    Add-Check 'wsl --version' ($w.ExitCode -eq 0) $script:Meta['wsl_version_text'] -KeepVersions
    $wv = $null
    if ($w.Output -match 'WSL version:\s*([0-9]+(\.[0-9]+)*)') { $wv = [version]$Matches[1] }
    $mirrored = $false
    if ($null -ne $wv -and $wv -ge [version]'2.0.0' -and $script:Meta['build_number'] -ge 22621) { $mirrored = $true }
    $script:Meta['mirrored_networking_available'] = $mirrored
}

function Save-Rootfs {
    if (-not (Test-Path -LiteralPath $script:RootfsFile)) {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
        $wc = New-Object Net.WebClient
        $wc.DownloadFile($script:RootfsUrl, $script:RootfsFile)
    }
    $h = (Get-FileHash -LiteralPath $script:RootfsFile -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($h -ne $script:RootfsSha256) {
        Remove-Item -LiteralPath $script:RootfsFile -Force
        throw 'rootfs SHA-256 does not match the pinned value'
    }
}

$script:RunScript = @'
#!/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
date +%s > /root/spike-started
mkdir -p /srv/www
echo povengine-spike > /srv/www/index.html
/bin/busybox httpd -p 18888 -h /srv/www
exec /usr/bin/dockerd > /var/log/dockerd.log 2>&1
'@

function Install-Distro {
    # Runs as whichever account will own the distribution.
    Save-Rootfs
    Add-Check 'rootfs sha256 matches the pinned value' $true ''
    $dir = Join-Path $env:LOCALAPPDATA 'povengine-spike\distro'
    [void](New-Item -ItemType Directory -Path $dir -Force)
    $imp = Invoke-WslHost -ArgList @('--import', $script:Distro, $dir, $script:RootfsFile, '--version', '2') -TimeoutSec 600
    Add-Check 'wsl --import (version 2)' ($imp.ExitCode -eq 0) ('exit ' + $imp.ExitCode + ' ' + $imp.Output)
    if ($imp.ExitCode -ne 0) { return $false }
    $apk = Invoke-WslIn -ArgList @('/sbin/apk', 'add', '--no-cache', 'docker') -TimeoutSec 600
    Add-Check 'apk add docker' ($apk.ExitCode -eq 0) ('exit ' + $apk.ExitCode + ' ' + ($apk.Output.Substring([Math]::Max(0, $apk.Output.Length - 120))))
    if ($apk.ExitCode -ne 0) { return $false }
    $put = Invoke-WslIn -ArgList @('/bin/sh', '-c', 'cat > /root/spike-run.sh') -StdinText ($script:RunScript -replace "`r`n", "`n")
    Add-Check 'run script written into the distribution' ($put.ExitCode -eq 0) ('exit ' + $put.ExitCode)
    return ($put.ExitCode -eq 0)
}

function Wait-DockerReady {
    param([int]$MaxSeconds = 120)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $MaxSeconds) {
        $r = Invoke-WslIn -ArgList @('/usr/bin/docker', 'info') -TimeoutSec 30
        if ($r.ExitCode -eq 0) {
            $ver = ''
            if ($r.Output -match 'Server Version:\s*(\S+)') { $ver = $Matches[1] }
            return [pscustomobject]@{ Ok = $true; Seconds = [int]$sw.Elapsed.TotalSeconds; Version = $ver }
        }
        Start-Sleep -Seconds 3
    }
    return [pscustomobject]@{ Ok = $false; Seconds = [int]$sw.Elapsed.TotalSeconds; Version = '' }
}

function Test-HelloWorld {
    $r = Invoke-WslIn -ArgList @('/usr/bin/docker', 'run', '--rm', 'hello-world') -TimeoutSec 300
    $ok = ($r.ExitCode -eq 0 -and $r.Output -match 'Hello from Docker')
    Add-Check 'docker run --rm hello-world' $ok ('exit ' + $r.ExitCode)
}

function Test-LocalHttp {
    param([string]$Label)
    $ok = $false
    $detail = ''
    try {
        $wc = New-Object Net.WebClient
        $body = $wc.DownloadString('http://127.0.0.1:' + $script:HttpPort + '/')
        $ok = ($body -match 'povengine-spike')
    }
    catch { $detail = $_.Exception.Message }
    Add-Check $Label $ok $detail
}

# ---- the Windows service (C#, compiled once; throwaway) ----------------------
$script:ServiceSource = @'
using System;
using System.Diagnostics;
using System.IO;
using System.ServiceProcess;
using System.Text.RegularExpressions;
using System.Threading;

public class SpikeSvc : ServiceBase
{
    const string Distro = "povengine-spike";
    const string LogPath = @"C:\ProgramData\povengine-spike\service-log.txt";
    Process wsl;
    Thread worker;
    volatile bool stopping;

    public SpikeSvc() { ServiceName = "povengine-spike"; CanStop = true; }

    static void Log(string m)
    {
        try { File.AppendAllText(LogPath, DateTime.UtcNow.ToString("o") + " " + m + "\r\n"); } catch (Exception) { }
    }

    static string Run(string file, string args, int timeoutMs, out int code)
    {
        ProcessStartInfo psi = new ProcessStartInfo(file, args);
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardOutput = true;
        psi.StandardOutputEncoding = System.Text.Encoding.UTF8;
        Process p = Process.Start(psi);
        string o = p.StandardOutput.ReadToEnd();
        if (!p.WaitForExit(timeoutMs)) { try { p.Kill(); } catch (Exception) { } code = -1; return o; }
        code = p.ExitCode;
        return o;
    }

    static string WslExe { get { return Path.Combine(Environment.SystemDirectory, "wsl.exe"); } }

    protected override void OnStart(string[] a)
    {
        int explorers = Process.GetProcessesByName("explorer").Length;
        Log("start explorer_processes=" + explorers + " identity_is_system=" + (System.Security.Principal.WindowsIdentity.GetCurrent().IsSystem));
        ProcessStartInfo psi = new ProcessStartInfo(WslExe, "-d " + Distro + " -u root --exec /bin/sh /root/spike-run.sh");
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;
        psi.RedirectStandardInput = true;
        wsl = Process.Start(psi);
        Log("wsl child started");
        worker = new Thread(Work);
        worker.IsBackground = true;
        worker.Start();
    }

    void Work()
    {
        int code;
        DateTime t0 = DateTime.UtcNow;
        bool ready = false;
        while (!stopping && (DateTime.UtcNow - t0).TotalSeconds < 180)
        {
            Run(WslExe, "-d " + Distro + " -u root --exec /usr/bin/docker info", 30000, out code);
            if (code == 0) { ready = true; break; }
            Thread.Sleep(3000);
        }
        Log("docker_ready=" + ready + " seconds=" + (int)(DateTime.UtcNow - t0).TotalSeconds);
        if (!ready) { return; }
        string o = Run(WslExe, "-d " + Distro + " -u root --exec /usr/bin/docker run --rm hello-world", 300000, out code);
        Log("hello_world_ok=" + (code == 0 && o.Contains("Hello from Docker")));
        o = Run(WslExe, "-d " + Distro + " -u root --exec /bin/hostname -I", 30000, out code);
        Match m = Regex.Match(o, @"\d{1,3}(\.\d{1,3}){3}");
        if (m.Success)
        {
            Run("netsh.exe", "interface portproxy delete v4tov4 listenport=18888 listenaddress=0.0.0.0", 30000, out code);
            Run("netsh.exe", "interface portproxy add v4tov4 listenport=18888 listenaddress=0.0.0.0 connectport=18888 connectaddress=" + m.Value, 30000, out code);
            Log("portproxy_applied=" + (code == 0));
        }
        else { Log("portproxy_applied=False no_address"); }
        while (!stopping)
        {
            for (int i = 0; i < 30 && !stopping; i++) { Thread.Sleep(1000); }
            if (stopping) { break; }
            Run(WslExe, "-d " + Distro + " -u root --exec /usr/bin/docker info", 30000, out code);
            Log("alive docker_info_ok=" + (code == 0));
        }
    }

    protected override void OnStop()
    {
        stopping = true;
        Log("stop requested");
        try { if (wsl != null && !wsl.HasExited) { wsl.Kill(); } } catch (Exception) { }
        int code;
        Run(WslExe, "--terminate " + Distro, 20000, out code);
        Log("stopped");
    }

    public static void Main() { ServiceBase.Run(new SpikeSvc()); }
}
'@

function Build-ServiceExe {
    if (Test-Path -LiteralPath $script:SvcExe) { Remove-Item -LiteralPath $script:SvcExe -Force }
    Add-Type -TypeDefinition $script:ServiceSource -OutputAssembly $script:SvcExe -OutputType WindowsApplication -ReferencedAssemblies @('System.ServiceProcess')
    Add-Check 'service executable compiled' (Test-Path -LiteralPath $script:SvcExe) ''
}

# ---- accounts, rights, one-shot tasks ----------------------------------------
function New-RandomSecret {
    # Built straight into a SecureString; never held as plain text.
    $set = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789'
    $bytes = New-Object byte[] 24
    $rng = New-Object Security.Cryptography.RNGCryptoServiceProvider
    $rng.GetBytes($bytes)
    $ss = New-Object Security.SecureString
    foreach ($c in 'Aa2!'.ToCharArray()) { $ss.AppendChar($c) }
    foreach ($b in $bytes) { $ss.AppendChar($set[[int]$b % $set.Length]) }
    $ss.MakeReadOnly()
    return $ss
}

function Grant-Rights {
    param([string]$Sid, [string[]]$Rights)
    $tmp = Join-Path $env:TEMP ('sec-' + [guid]::NewGuid().ToString('N'))
    $inf = $tmp + '.inf'
    $db = $tmp + '.sdb'
    [void](& secedit.exe /export /cfg $inf /areas USER_RIGHTS)
    $lines = New-Object System.Collections.ArrayList
    foreach ($l in @(Get-Content -LiteralPath $inf)) { [void]$lines.Add([string]$l) }
    foreach ($r in $Rights) {
        $found = $false
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match ('^' + $r + '\s*=')) {
                if ($lines[$i] -notmatch [regex]::Escape($Sid)) { $lines[$i] = $lines[$i] + ',*' + $Sid }
                $found = $true
            }
        }
        if (-not $found) {
            $at = $lines.IndexOf('[Privilege Rights]')
            if ($at -lt 0) { throw 'no [Privilege Rights] section in secedit export' }
            $lines.Insert($at + 1, ($r + ' = *' + $Sid))
        }
    }
    Set-Content -LiteralPath $inf -Value $lines.ToArray() -Encoding Unicode
    [void](& secedit.exe /configure /db $db /cfg $inf /areas USER_RIGHTS)
    $rc = $LASTEXITCODE
    Remove-Item -LiteralPath $inf, $db -Force -ErrorAction SilentlyContinue
    return ($rc -eq 0)
}

function Invoke-AsAccount {
    # Run this script's internal mode under another account through a one-shot
    # scheduled task, and wait for it. Distributions are per account.
    # No credential means SYSTEM.
    param([Management.Automation.PSCredential]$Credential = $null, [string[]]$ScriptArgs, [int]$TimeoutSec = 900)
    $name = 'povengine-spike-oneshot'
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    $argText = '-NoProfile -ExecutionPolicy Bypass -File "' + $script:StagedScript + '" ' + ($ScriptArgs -join ' ')
    $act = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -Argument $argText
    $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries
    if ($null -ne $Credential) {
        $task = New-ScheduledTask -Action $act -Settings $set
        # The one API that needs the secret as text.
        [void](Register-ScheduledTask -TaskName $name -InputObject $task -User $Credential.UserName -Password ($Credential.GetNetworkCredential().Password))
    }
    else {
        $pr = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        [void](Register-ScheduledTask -TaskName $name -Action $act -Settings $set -Principal $pr)
    }
    Start-ScheduledTask -TaskName $name
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Start-Sleep -Seconds 3
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $st = (Get-ScheduledTask -TaskName $name).State
        if ($st -ne 'Running' -and $st -ne 'Queued') { break }
        Start-Sleep -Seconds 3
    }
    $info = Get-ScheduledTaskInfo -TaskName $name
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    return [int]$info.LastTaskResult
}

function Read-ChildResult {
    param([string]$Path, [string]$Prefix)
    if (-not (Test-Path -LiteralPath $Path)) { Add-Check ($Prefix + ': child result file') $false 'missing'; return $false }
    $o = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    foreach ($c in @($o.checks)) { Add-Check ($Prefix + ': ' + $c.check) ([bool]$c.ok) ([string]$c.detail) }
    return [bool]$o.ok
}

function Set-PortAccess {
    [void](& netsh.exe advfirewall firewall delete rule "name=$($script:FwRule)")
    [void](& netsh.exe advfirewall firewall add rule "name=$($script:FwRule)" dir=in action=allow protocol=TCP "localport=$($script:HttpPort)")
    Add-Check 'inbound firewall rule for the test port' ($LASTEXITCODE -eq 0) ''
}

# ---- modes -------------------------------------------------------------------
function Invoke-Probe {
    Initialize-WslHost
    if ($script:Meta['wsl_version_exit'] -ne 0) { return $false }
    if (-not (Install-Distro)) { return $false }
    $holder = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\wsl.exe') -ArgumentList @('-d', $script:Distro, '-u', 'root', '--exec', '/bin/sh', '/root/spike-run.sh') -WindowStyle Hidden -PassThru
    try {
        $dk = Wait-DockerReady -MaxSeconds 120
        Add-Check 'docker info (dockerd answers)' $dk.Ok ('server version ' + $dk.Version + ' after ' + $dk.Seconds + ' s')
        if (-not $dk.Ok) { return $false }
        Test-HelloWorld
    }
    finally {
        if ($holder -and -not $holder.HasExited) { try { $holder.Kill() } catch { Write-Verbose 'holder kill failed' } }
        [void](Invoke-WslHost -ArgList @('--terminate', $script:Distro) -TimeoutSec 60)
    }
    foreach ($c in $script:Checks) { if (-not $c['ok'] -and $c['check'] -notmatch 'wsl --update|set-default-version') { return $false } }
    return $true
}

function Get-ServiceLogSinceBoot {
    $boot = (Get-CimInstance -ClassName Win32_OperatingSystem).LastBootUpTime.ToUniversalTime()
    if (-not (Test-Path -LiteralPath $script:SvcLog)) { return @() }
    $out = @()
    foreach ($l in @(Get-Content -LiteralPath $script:SvcLog)) {
        if ($l -match '^(\S+)\s+(.*)$') {
            $ts = [DateTime]::Parse($Matches[1], [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
            if ($ts -gt $boot) { $out += $Matches[2] }
        }
    }
    return $out
}

function Test-ServiceLog {
    param([string[]]$Lines)
    $joined = ($Lines -join "`n")
    $m = [regex]::Match($joined, 'start explorer_processes=(\d+)')
    $noUser = ($m.Success -and $m.Groups[1].Value -eq '0')
    Add-Check 'service started with no interactive session (explorer count 0 at start)' $noUser $m.Value
    $dr = ($joined -match 'docker_ready=True')
    $secs = ''
    if ($joined -match 'docker_ready=True seconds=(\d+)') { $secs = $Matches[1] }
    Add-Check 'starts from service with nobody signed in: dockerd answers inside the distribution' $dr ('after ' + $secs + ' s')
    Add-Check 'docker run hello-world from the service account' ($joined -match 'hello_world_ok=True') ''
    Add-Check 'service re-applied the port forward by itself' ($joined -match 'portproxy_applied=True') ''
    $alive = [regex]::Matches($joined, 'alive docker_info_ok=(True|False)')
    $last = $null
    if ($alive.Count -gt 0) { $last = $alive[$alive.Count - 1].Groups[1].Value }
    return [pscustomobject]@{ NoUser = $noUser; Docker = $dr; AliveLast = $last; AliveCount = $alive.Count }
}

function Invoke-ServiceMode {
    $isAccount = ($Mode -eq 'account')
    Initialize-WslHost
    if ($script:Meta['wsl_version_exit'] -ne 0) { return $false }
    Save-Rootfs
    Copy-Item -LiteralPath $PSCommandPath -Destination $script:StagedScript -Force
    $childJson = Join-Path $script:WorkDir 'provision-child.json'
    New-StagedFile $script:SvcLog
    New-StagedFile $childJson

    $cred = $null
    if ($isAccount) {
        $secret = New-RandomSecret
        if (Get-LocalUser -Name $script:AccountName -ErrorAction SilentlyContinue) { Remove-LocalUser -Name $script:AccountName }
        $u = New-LocalUser -Name $script:AccountName -Password $secret -PasswordNeverExpires -UserMayNotChangePassword -Description 'POVengine spike service account'
        Add-Check 'local account created (random password)' $true ''
        $rights = Grant-Rights -Sid $u.SID.Value -Rights @('SeServiceLogonRight', 'SeBatchLogonRight')
        Add-Check 'Log on as a service (and batch) granted' $rights ''
        # Least access: traverse the directory, read what it must run, write
        # only the two files it must write. Nothing else in the directory.
        $g1 = Grant-FileAccess $script:WorkDir 'RX'
        $g2 = Grant-FileAccess $script:StagedScript 'RX'
        $g3 = Grant-FileAccess $script:RootfsFile 'RX'
        $g4 = Grant-FileAccess $childJson 'M'
        $g5 = Grant-FileAccess $script:SvcLog 'M'
        Add-Check 'account granted read/execute on three paths and write on two result files only' ($g1 -and $g2 -and $g3 -and $g4 -and $g5) ''
        $cred = New-Object Management.Automation.PSCredential (('.\' + $script:AccountName), $secret)
    }
    $rc = Invoke-AsAccount -Credential $cred -ScriptArgs @('-Mode', 'probe', '-ProvisionOnly', '-ResultPath', ('"' + $childJson + '"'))
    Add-Check ('provisioning task exit code ' + $rc) ($rc -eq 0) ''
    $okChild = Read-ChildResult -Path $childJson -Prefix 'as service identity'
    if (-not $okChild) { return $false }

    Build-ServiceExe
    if ($isAccount) { Add-Check 'account granted read/execute on the service executable' (Grant-FileAccess $script:SvcExe 'RX') '' }
    Set-PortAccess
    if (Get-Service -Name $script:SvcName -ErrorAction SilentlyContinue) { Stop-Service -Name $script:SvcName -Force -ErrorAction SilentlyContinue; [void](& sc.exe delete $script:SvcName) }
    if ($isAccount) {
        New-Service -Name $script:SvcName -BinaryPathName $script:SvcExe -DisplayName 'POVengine spike' -StartupType Automatic -Credential $cred | Out-Null
    }
    else {
        New-Service -Name $script:SvcName -BinaryPathName $script:SvcExe -DisplayName 'POVengine spike' -StartupType Automatic | Out-Null
    }
    $startOk = $true
    $startDetail = ''
    try { Start-Service -Name $script:SvcName } catch { $startOk = $false; $startDetail = $_.Exception.Message }
    Add-Check 'service starts under the SCM' $startOk $startDetail
    if (-not $startOk) { return $false }

    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt 240) {
        if ((Test-Path -LiteralPath $script:SvcLog) -and ((Get-Content -LiteralPath $script:SvcLog -Raw) -match 'portproxy_applied|docker_ready=False')) { break }
        Start-Sleep -Seconds 3
    }
    Start-Sleep -Seconds 5
    $lines = @(Get-Content -LiteralPath $script:SvcLog | ForEach-Object { ($_ -replace '^\S+\s+', '') })
    $sum = Test-ServiceLog -Lines $lines
    Test-LocalHttp 'busybox httpd reachable through the portproxy (from this host)'

    Start-Sleep -Seconds $IdleWaitSeconds
    $svc = Get-Service -Name $script:SvcName
    $lines2 = @(Get-Content -LiteralPath $script:SvcLog | ForEach-Object { ($_ -replace '^\S+\s+', '') })
    $sum2 = Test-ServiceLog -Lines $lines2
    $alive = ($svc.Status -eq 'Running' -and $sum2.AliveCount -gt 0 -and $sum2.AliveLast -eq 'True')
    Add-Check ('no idle shutdown after ' + $IdleWaitSeconds + ' s with only the service attached') $alive ('service ' + $svc.Status + ', liveness probes ' + $sum2.AliveCount)
    Test-LocalHttp 'busybox httpd still reachable after the idle wait'
    $script:Meta['idle_wait_seconds'] = $IdleWaitSeconds
    Write-Host 'MANUAL: from ANOTHER machine fetch http://<this host>:18888/ and record the result in the results doc.'
    return ($sum.Docker -and $alive)
}

function Invoke-LogonMode {
    Initialize-WslHost
    if ($script:Meta['wsl_version_exit'] -ne 0) { return $false }
    if (-not (Install-Distro)) { return $false }
    Set-PortAccess
    Unregister-ScheduledTask -TaskName $script:TaskName -Confirm:$false -ErrorAction SilentlyContinue
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $act = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\wsl.exe') -Argument ('-d ' + $script:Distro + ' -u root --exec /bin/sh /root/spike-run.sh')
    $trg = New-ScheduledTaskTrigger -AtLogOn -User $me
    $set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $pr = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
    [void](Register-ScheduledTask -TaskName $script:TaskName -Action $act -Trigger $trg -Settings $set -Principal $pr)
    Add-Check 'sign-in task registered' $true ''
    Start-ScheduledTask -TaskName $script:TaskName
    $dk = Wait-DockerReady -MaxSeconds 120
    Add-Check 'dockerd answers inside the distribution (task running, this account signed in)' $dk.Ok ('server version ' + $dk.Version + ' after ' + $dk.Seconds + ' s')
    if (-not $dk.Ok) { return $false }
    Test-HelloWorld
    $ip = (Invoke-WslIn -ArgList @('/bin/hostname', '-I')).Output
    $pp = $false
    if ($ip -match '\d{1,3}(\.\d{1,3}){3}') {
        $addr = $Matches[0]
        [void](& netsh.exe interface portproxy delete v4tov4 "listenport=$($script:HttpPort)" listenaddress=0.0.0.0)
        [void](& netsh.exe interface portproxy add v4tov4 "listenport=$($script:HttpPort)" listenaddress=0.0.0.0 "connectport=$($script:HttpPort)" "connectaddress=$addr")
        $pp = ($LASTEXITCODE -eq 0)
    }
    Add-Check 'port forward applied' $pp ''
    Test-LocalHttp 'busybox httpd reachable through the portproxy (from this host)'
    Start-Sleep -Seconds $IdleWaitSeconds
    $r = Invoke-WslIn -ArgList @('/usr/bin/docker', 'info') -TimeoutSec 30
    Add-Check ('no idle shutdown after ' + $IdleWaitSeconds + ' s with only the task attached') ($r.ExitCode -eq 0) ''
    $script:Meta['idle_wait_seconds'] = $IdleWaitSeconds
    Write-Host 'MANUAL: from ANOTHER machine fetch http://<this host>:18888/ and record the result in the results doc.'
    Write-Host 'MANUAL: for the starts-with-nobody-signed-in row, logon mode is expected to fail by design (the task needs a sign-in).'
    return $dk.Ok
}

function Invoke-PostReboot {
    Get-WinInfo | Out-Null
    $script:Meta['last_boot_utc'] = (Get-CimInstance -ClassName Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
    if ($Mode -eq 'logon') {
        $info = $null
        try { $info = Get-ScheduledTaskInfo -TaskName $script:TaskName } catch { Write-Verbose 'task missing' }
        $ran = ($null -ne $info -and $info.LastRunTime.ToUniversalTime() -gt (Get-CimInstance -ClassName Win32_OperatingSystem).LastBootUpTime.ToUniversalTime())
        Add-Check 'sign-in task ran again after the reboot' $ran ''
        $dk = Wait-DockerReady -MaxSeconds 60
        Add-Check 'dockerd answers after the reboot (signed in)' $dk.Ok ('after ' + $dk.Seconds + ' s')
        return $dk.Ok
    }
    $svc = Get-Service -Name $script:SvcName -ErrorAction SilentlyContinue
    $running = ($null -ne $svc -and $svc.Status -eq 'Running')
    Add-Check 'service is Running after the reboot' $running ''
    $lines = @(Get-ServiceLogSinceBoot)
    $sum = Test-ServiceLog -Lines $lines
    Test-LocalHttp 'busybox httpd reachable through the portproxy after the reboot'
    return ($running -and $sum.Docker)
}

function Test-DistroPresent {
    $l = Invoke-WslHost -ArgList @('--list', '--quiet') -TimeoutSec 60
    foreach ($line in ($l.Output -split '[\r\n]+')) { if ($line.Trim() -eq $script:Distro) { return $true } }
    return $false
}

function Invoke-Cleanup {
    if ($LocalDistroOnly) {
        [void](Invoke-WslHost -ArgList @('--unregister', $script:Distro) -TimeoutSec 120)
        $gone = -not (Test-DistroPresent)
        Add-Check 'distribution absent for this account' $gone ''
        $dir = Join-Path $env:LOCALAPPDATA 'povengine-spike'
        $dirGone = $true
        if (Test-Path -LiteralPath $dir) {
            try { Remove-Item -LiteralPath $dir -Recurse -Force } catch { Write-Verbose 'remove failed' }
            $dirGone = -not (Test-Path -LiteralPath $dir)
        }
        Add-Check 'per-account distribution directory absent' $dirGone ''
        return ($gone -and $dirGone)
    }

    # service
    $detail = ''
    try {
        if (Get-Service -Name $script:SvcName -ErrorAction SilentlyContinue) {
            Stop-Service -Name $script:SvcName -Force -ErrorAction SilentlyContinue
            [void](& sc.exe delete $script:SvcName)
        }
    }
    catch { $detail = $_.Exception.Message }
    for ($i = 0; $i -lt 10; $i++) {
        if (-not (Get-Service -Name $script:SvcName -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 1
    }
    Add-Check 'service absent' ($null -eq (Get-Service -Name $script:SvcName -ErrorAction SilentlyContinue)) $detail

    # tasks
    foreach ($t in @($script:TaskName, 'povengine-spike-oneshot')) {
        try { Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue } catch { Write-Verbose 'no task' }
        Add-Check ('task ' + $t + ' absent') ($null -eq (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue)) ''
    }

    # portproxy and firewall
    [void](& netsh.exe interface portproxy delete v4tov4 "listenport=$($script:HttpPort)" listenaddress=0.0.0.0)
    $show = (@(& netsh.exe interface portproxy show v4tov4) -join "`n")
    Add-Check 'portproxy rule absent' ($show -notmatch ('\s' + $script:HttpPort + '\s')) ''
    [void](& netsh.exe advfirewall firewall delete rule "name=$($script:FwRule)")
    [void](& netsh.exe advfirewall firewall show rule "name=$($script:FwRule)")
    Add-Check 'firewall rule absent' ($LASTEXITCODE -ne 0) ''

    # distribution under SYSTEM
    Copy-Item -LiteralPath $PSCommandPath -Destination $script:StagedScript -Force
    $sysJson = Join-Path $script:WorkDir 'cleanup-system.json'
    New-StagedFile $sysJson
    $rc = Invoke-AsAccount -ScriptArgs @('-Mode', 'cleanup', '-LocalDistroOnly', '-ResultPath', ('"' + $sysJson + '"')) -TimeoutSec 300
    Add-Check 'SYSTEM distribution cleanup task exit code' ($rc -eq 0) ('exit ' + $rc)
    [void](Read-ChildResult -Path $sysJson -Prefix 'SYSTEM')

    # the spike account, its distribution and its profile
    $acct = Get-LocalUser -Name $script:AccountName -ErrorAction SilentlyContinue
    if ($acct) {
        $secret = New-RandomSecret
        Set-LocalUser -Name $script:AccountName -Password $secret
        $cred = New-Object Management.Automation.PSCredential (('.\' + $script:AccountName), $secret)
        $accJson = Join-Path $script:WorkDir 'cleanup-account.json'
        New-StagedFile $accJson
        $g1 = Grant-FileAccess $script:WorkDir 'RX'
        $g2 = Grant-FileAccess $script:StagedScript 'RX'
        $g3 = Grant-FileAccess $accJson 'M'
        Add-Check 'account granted read/execute and one result file for its cleanup task' ($g1 -and $g2 -and $g3) ''
        $rc2 = Invoke-AsAccount -Credential $cred -ScriptArgs @('-Mode', 'cleanup', '-LocalDistroOnly', '-ResultPath', ('"' + $accJson + '"')) -TimeoutSec 300
        Add-Check 'account distribution cleanup task exit code' ($rc2 -eq 0) ('exit ' + $rc2)
        [void](Read-ChildResult -Path $accJson -Prefix 'account')
        $sid = $acct.SID.Value
        $profPath = ''
        $profDetail = ''
        $prof = @(Get-CimInstance -ClassName Win32_UserProfile | Where-Object { $_.SID -eq $sid })
        if ($prof.Count -gt 0) {
            $profPath = [string]$prof[0].LocalPath
            try { $prof[0] | Remove-CimInstance } catch { $profDetail = $_.Exception.Message }
        }
        $profLeft = @(Get-CimInstance -ClassName Win32_UserProfile | Where-Object { $_.SID -eq $sid })
        $profOk = ($profLeft.Count -eq 0) -and (-not ($profPath -and (Test-Path -LiteralPath $profPath)))
        Add-Check 'account profile absent' $profOk $profDetail
        $rmDetail = ''
        try { Remove-LocalUser -Name $script:AccountName } catch { $rmDetail = $_.Exception.Message }
        Add-Check 'local account absent (the Log-on-as-service right is left as an orphan SID)' ($null -eq (Get-LocalUser -Name $script:AccountName -ErrorAction SilentlyContinue)) $rmDetail
    }
    else { Add-Check 'local account absent' $true 'it did not exist' }

    # distribution under the invoking account
    [void](Invoke-WslHost -ArgList @('--unregister', $script:Distro) -TimeoutSec 120)
    Add-Check 'distribution absent for the invoking account' (-not (Test-DistroPresent)) ''

    # work directory last: the child results above live in it
    $wdDetail = ''
    try { Remove-Item -LiteralPath $script:WorkDir -Recurse -Force } catch { $wdDetail = $_.Exception.Message }
    Add-Check 'work directory absent' (-not (Test-Path -LiteralPath $script:WorkDir)) $wdDetail

    foreach ($c in $script:Checks) { if (-not $c['ok']) { return $false } }
    return $true
}

# ---- main ----------------------------------------------------------------------
function Resolve-Outcome {
    # A mode's verdict is the last item it emitted, and only a real $true counts,
    # so stray pipeline output can never turn a failed run into exit 0.
    param($Out)
    $a = @($Out)
    if ($a.Count -eq 0) { return $false }
    $last = $a[$a.Count - 1]
    return (($last -is [bool]) -and [bool]$last)
}

$overall = $false
try {
    if (-not ($ProvisionOnly -or $LocalDistroOnly)) {
        if (-not (Test-Admin)) { throw 'run this from an elevated Windows PowerShell' }
        Initialize-WorkDir
    }
    if ($Mode -eq 'cleanup') { $overall = Resolve-Outcome (Invoke-Cleanup) }
    elseif ($PostReboot) { $overall = Resolve-Outcome (Invoke-PostReboot) }
    elseif ($Mode -eq 'probe') {
        if ($ProvisionOnly) {
            Get-WinInfo | Out-Null
            $overall = Resolve-Outcome (Install-Distro)
        }
        else { $overall = Resolve-Outcome (Invoke-Probe) }
    }
    elseif ($Mode -eq 'logon') { $overall = Resolve-Outcome (Invoke-LogonMode) }
    else { $overall = Resolve-Outcome (Invoke-ServiceMode) }
}
catch {
    Add-Check 'unexpected error' $false $_.Exception.Message
    $overall = $false
}
finally {
    if (-not $script:Meta.Contains('build')) { try { Get-WinInfo | Out-Null } catch { Write-Verbose 'no win info' } }
    Write-Result -Ok ([bool]$overall)
}
if ($overall) { exit 0 }
exit 1
