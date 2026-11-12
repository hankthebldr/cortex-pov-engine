# A Windows PowerShell 5.1-clean script. scripts/ci/ps51-gate.ps1 MUST pass it.
# Every line is a near neighbour of a PS7-only construct the gate rejects, so a
# rule that over-matches goes red here.
$ErrorActionPreference = 'Stop'
$cfg = if ($env:CORTEXSIM_CFG) { $env:CORTEXSIM_CFG } else { 'default' }
if (Test-Path $cfg) { Write-Output 'present' }
$obj = '{"a": 1}' | ConvertFrom-Json
$json = ConvertTo-Json -InputObject @($obj) -Depth 4 -Compress
$p = Join-Path $env:TEMP 'cortexsim'
$q = Join-Path -Path $p -ChildPath 'x.txt'
$leaf = Split-Path -Leaf $q
$parent = Split-Path -Parent $q
Set-Content -LiteralPath $q -Value $json -Encoding UTF8
$text = Get-Content -LiteralPath $q -Raw -Encoding UTF8
$items = 1..3 | ForEach-Object -Process { $_ * 2 }
$null = Invoke-WebRequest -Uri 'http://127.0.0.1:8888/api/health' -UseBasicParsing -TimeoutSec 5 -MaximumRedirection 0
$keys = $obj.a
$x = @{ a = 1 }['a']
# The ONE allowlisted use of the PS6+ platform variables: the push harness's
# dry-run branch. On 5.1 both are undefined, so $null, so the Windows branch.
if ($IsLinux -or $IsMacOS) {
    Write-Output 'dry run off-Windows'
} else {
    Write-Output 'windows'
}
Write-Host "[cortexsim] done - plain ASCII in a double-quoted string, $leaf"
