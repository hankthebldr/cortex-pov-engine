package executor

// TestDumpPowerShellSamples writes the PowerShell the Windows beacon GENERATES
// to disk, so scripts/ci/ps51-gate.ps1 can parse it the way Windows PowerShell
// 5.1 would. It is not an assertion about behaviour -- the round-trip tests in
// powershell_exec_test.go are -- it is the render step of a gate whose
// assertions live in PowerShell.
//
// It is driven by scripts/ci/render_powershell.py (`make check-powershell`)
// and skips unless CORTEXSIM_PS_DUMP names the output directory, so a plain
// `go test ./...` is unaffected.
//
// It calls the CURRENT signatures directly:
//
//	powerShellExitWrapper(cmd string) string
//	powerShellChainStepScript(marker, wrappedCmd string) string
//
// If either changes, this file must change with it -- that is deliberate: a
// gate that silently stopped rendering the beacon's wrapper would read green.
//
// Encodings mirror delivery, because the gate decodes the way 5.1 reads:
//   - the exit wrapper reaches powershell.exe through -EncodedCommand
//     (UTF-16LE), i.e. exact text, so it is written with a UTF-8 BOM;
//   - the chain step script reaches the anchor through stdin of
//     `powershell.exe -Command -`, decoded with the console code page, so it is
//     written BOM-less (it must be ASCII to survive that, and the gate will
//     say so if it is not);
//   - each chain step's -EncodedCommand payload is decoded back out and
//     written with a BOM, which also proves the encoder round-trips.

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"unicode/utf16"
)

// psDumpSampleBody is shaped like the corpus' Windows steps: cmdlets, $env:,
// quoting that breaks naive layers, a native exe with a non-zero exit, a
// here-string, and one non-ASCII character inside a double-quoted string.
const psDumpSampleBody = `$p = Join-Path $env:TEMP 'cortexsim-psdump.txt'
"cortexsim marker ` + "\u2014" + ` em dash" | Out-File -FilePath $p -Encoding UTF8
$text = @'
literal $notavar "quoted" ` + "`" + `backtick & | > %PATH%
'@
Get-Item -LiteralPath $p | Select-Object -ExpandProperty Length
& cmd.exe /c "exit 3"`

var psDumpEncodedRE = regexp.MustCompile(`-EncodedCommand ([A-Za-z0-9+/=]+)`)

func TestDumpPowerShellSamples(t *testing.T) {
	dir := os.Getenv("CORTEXSIM_PS_DUMP")
	if dir == "" {
		t.Skip("CORTEXSIM_PS_DUMP unset -- render step of `make check-powershell` only")
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", dir, err)
	}
	bom := "\ufeff"
	write := func(name, body string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
			t.Fatalf("write %s: %v", name, err)
		}
	}

	// 1. The exit-code wrapper every Windows step runs inside.
	write("exit_wrapper_sample.ps1", bom+powerShellExitWrapper(psDumpSampleBody))
	write("exit_wrapper_empty.ps1", bom+powerShellExitWrapper(""))

	// 2. A two-step anchor transcript, exactly as ChainSession writes it.
	const marker = "__CORTEXSIM_STEP_4242__"
	steps := []string{psDumpSampleBody, "Write-Output 'second step'\nexit 7"}
	var chain strings.Builder
	for _, s := range steps {
		chain.WriteString(powerShellChainStepScript(marker, s))
	}
	// anchorExitScript is per-platform (platform_{unix,windows}.go); both are
	// "exit\n" today. On the rendering host this is the unix constant -- run
	// the dump with GOOS=windows on a Windows runner to render the other.
	chain.WriteString(anchorExitScript)
	write("chain_two_steps.ps1", chain.String())

	// 3. The payloads the anchor hands each child, decoded back to text.
	found := psDumpEncodedRE.FindAllStringSubmatch(chain.String(), -1)
	if len(found) != len(steps) {
		t.Fatalf("expected %d -EncodedCommand payloads in the chain script, found %d", len(steps), len(found))
	}
	for i, m := range found {
		raw, err := base64.StdEncoding.DecodeString(m[1])
		if err != nil {
			t.Fatalf("payload %d is not base64: %v", i+1, err)
		}
		if len(raw)%2 != 0 {
			t.Fatalf("payload %d is not UTF-16 (odd length %d)", i+1, len(raw))
		}
		units := make([]uint16, len(raw)/2)
		for j := range units {
			units[j] = uint16(raw[2*j]) | uint16(raw[2*j+1])<<8 // little-endian
		}
		text := string(utf16.Decode(units))
		if want := powerShellExitWrapper(steps[i]); text != want {
			t.Fatalf("payload %d does not round-trip through encodePowerShellCommand", i+1)
		}
		write("chain_step_"+string(rune('0'+i+1))+"_payload.ps1", bom+text)
	}
}
