# Deliberately PowerShell-7-only. scripts/ci/ps51-gate.ps1 MUST reject every
# annotated line below with exactly the annotated rule and nothing else.
# Windows PowerShell 5.1 cannot run this file: most lines do not even parse.
# Excluded from the repo-ps1 category on purpose (it is a gate fixture).
$cfg = $env:CORTEXSIM_CFG ?? 'default'  # expect: PS7_NULL_COALESCE
$cfg ??= 'fallback'  # expect: PS7_NULL_COALESCE_ASSIGN
Test-Path $cfg && Write-Output 'present'  # expect: PS7_PIPELINE_CHAIN
Test-Path $cfg || Write-Output 'absent'  # expect: PS7_PIPELINE_CHAIN
$mode = (Test-Path $cfg) ? 'file' : 'env'  # expect: PS7_TERNARY
$obj = '{"a": 1}' | ConvertFrom-Json -AsHashtable  # expect: PS7_PARAMETER
$deep = '{"a": 1}' | ConvertFrom-Json -Depth 3  # expect: PS7_PARAMETER
$keys = ${obj}?.Keys  # expect: PS7_NULL_CONDITIONAL
$first = ${obj}?['a']  # expect: PS7_NULL_CONDITIONAL
if ($IsWindows) { 'windows' }  # expect: PS7_AUTOMATIC_VARIABLE
$reset = $PSStyle.Reset  # expect: PS7_AUTOMATIC_VARIABLE
$core = $IsCoreCLR  # expect: PS7_AUTOMATIC_VARIABLE
$arr = ConvertTo-Json -InputObject 1 -AsArray  # expect: PS7_PARAMETER
Invoke-WebRequest -Uri 'https://127.0.0.1/' -SkipCertificateCheck  # expect: PS7_PARAMETER
iwr -Uri 'https://127.0.0.1/' -SslProtocol Tls12  # expect: PS7_PARAMETER
Invoke-RestMethod -Uri 'https://127.0.0.1/' -Method Post -Form @{ a = 1 }  # expect: PS7_PARAMETER
irm -Uri 'https://127.0.0.1/' -MaximumRetryCount 3  # expect: PS7_PARAMETER
irm -Uri 'https://127.0.0.1/' -StatusCodeVariable sc  # expect: PS7_PARAMETER
$bytes = Get-Content -LiteralPath $cfg -AsByteStream  # expect: PS7_PARAMETER
Set-Content -LiteralPath $cfg -Value $bytes -AsByteStream  # expect: PS7_PARAMETER
1..3 | ForEach-Object -Parallel { $_ }  # expect: PS7_PARAMETER
$p = Join-Path 'a' -ChildPath 'b' -AdditionalChildPath 'c'  # expect: PS7_PARAMETER
$q = Join-Path 'a' 'b' 'c'  # expect: PS7_JOIN_PATH_CHILDREN
$base = Split-Path -LeafBase $q  # expect: PS7_PARAMETER
$ext = Split-Path -Extension $q  # expect: PS7_PARAMETER
Set-Content -LiteralPath $cfg -Value 'x' -Encoding utf8NoBOM  # expect: PS7_ENCODING_VALUE
Out-File -FilePath $cfg -InputObject 'x' -Encoding ansi  # expect: PS7_ENCODING_VALUE
Start-Sleep -Seconds 1 &  # expect: PS7_BACKGROUND
$dash = "`u{2014}"  # expect: PS7_UNICODE_ESCAPE
function Invoke-CsClean {
    process { 'work' }
    clean { 'tidy' }  # expect: PS7_CLEAN_BLOCK
}
