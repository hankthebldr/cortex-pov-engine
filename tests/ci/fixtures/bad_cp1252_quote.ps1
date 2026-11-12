# BOM-less UTF-8 with a non-ASCII em dash INSIDE a double-quoted string.
# Windows PowerShell 5.1 reads a BOM-less script file as Windows-1252, so the
# dash's third byte (0x94) decodes as U+201D, which PowerShell treats as a
# closing double quote: the string ends early and the file does not parse.
# The UTF-8 read of the same bytes (the iwr | iex path) parses cleanly.
# Excluded from the repo-ps1 category on purpose (it is a gate fixture).
$Interval = 10
Write-Host "[cortexsim] done — the agent should appear within $Interval s"
