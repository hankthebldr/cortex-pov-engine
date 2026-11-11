package executor

import (
	"strings"
	"testing"
)

// The two samples below are the bytes Windows PowerShell 5.1 wrote to a
// redirected stderr on the windows-2022 CI runner (runs 42927561143 and
// 42930052584, 2026-11-10), copied from the failing test output. They are the
// evidence the decoder is built against; do not tidy them.

// clixmlErrorSample is `Write-Error 'cortexsim-boom'` run through the exit
// wrapper with -OutputFormat Text -EncodedCommand.
const clixmlErrorSample = "#< CLIXML\r\n" +
	`<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">` +
	`<S S="Error">$ProgressPreference = 'SilentlyContinue'_x000D__x000A_</S>` +
	`<S S="Error">Write-Error 'cortexsim-boom'_x000D__x000A_</S>` +
	`<S S="Error">if ($null -ne $__cs_native -and $__cs_native -ne 0) { exit $__cs_native }_x000D__x000A_</S>` +
	`<S S="Error"> : cortexsim-boom_x000D__x000A_</S>` +
	`<S S="Error">    + CategoryInfo          : NotSpecified: (:) [Write-Error], WriteErrorException_x000D__x000A_</S>` +
	`<S S="Error">    + FullyQualifiedErrorId : Microsoft.PowerShell.Commands.WriteErrorException_x000D__x000A_</S>` +
	`<S S="Error"> _x000D__x000A_</S></Objs>`

// clixmlProgressSample is a clean `Write-Output hello` plus a raw
// [Console]::Error line: the only CLIXML is the first-use progress record.
const clixmlProgressSample = "#< CLIXML\n" +
	"oops\n" +
	`<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">` +
	`<Obj S="progress" RefId="0"><TN RefId="0"><T>System.Management.Automation.PSCustomObject</T><T>System.Object</T></TN>` +
	`<MS><I64 N="SourceId">1</I64><PR N="Record"><AV>Preparing modules for first use.</AV><AI>0</AI><Nil /><PI>-1</PI><PC>-1</PC>` +
	`<T>Completed</T><SR>-1</SR><SD> </SD></PR></MS></Obj></Objs>`

func TestDecodeCLIXMLStderr_ErrorRecordBecomesText(t *testing.T) {
	got := decodeCLIXMLStderr(clixmlErrorSample)
	if strings.Contains(got, "#< CLIXML") || strings.Contains(got, "<Objs") || strings.Contains(got, "</S>") {
		t.Fatalf("CLIXML framing survived:\n%s", got)
	}
	if strings.Contains(got, "_x000D_") || strings.Contains(got, "_x000A_") {
		t.Fatalf("_xHHHH_ escapes survived:\n%q", got)
	}
	for _, want := range []string{
		" : cortexsim-boom\r\n",
		"    + CategoryInfo          : NotSpecified: (:) [Write-Error], WriteErrorException\r\n",
		"    + FullyQualifiedErrorId : Microsoft.PowerShell.Commands.WriteErrorException\r\n",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("decoded stderr lacks %q:\n%q", want, got)
		}
	}
}

func TestDecodeCLIXMLStderr_ProgressOnlyDocumentIsDroppedRawTextKept(t *testing.T) {
	got := decodeCLIXMLStderr(clixmlProgressSample)
	if got != "oops\n" {
		t.Fatalf("got %q, want only the raw stderr line %q", got, "oops\n")
	}
}

func TestDecodeCLIXMLStderr_EntitiesAndLiteralUnderscoreX(t *testing.T) {
	in := "#< CLIXML\n" +
		`<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">` +
		`<S S="Error">a &lt;b&gt; &amp; "c" _x005F_x000D_ end_x000A_</S></Objs>` + "\n"
	want := "a <b> & \"c\" _x000D_ end\n"
	if got := decodeCLIXMLStderr(in); got != want {
		t.Fatalf("got %q, want %q", got, want)
	}
}

// Evidence is kept byte-for-byte: anything that is not the PowerShell CLIXML
// envelope must pass through untouched, including XML that merely looks close.
func TestDecodeCLIXMLStderr_LeavesEverythingElseAlone(t *testing.T) {
	for _, in := range []string{
		"",
		"plain error text\nsecond line\n",
		"<Objs>not the powershell namespace</Objs>\n",
		`<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04"><S S="Error">never closed`,
		"a line that mentions #< CLIXML in the middle\n",
		// A document with content this decoder does not know stays whole,
		// header included: an unknown shape must remain visible.
		"#< CLIXML\n" + `<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">` +
			`<S S="Error">known_x000A_</S><Obj S="Error" RefId="0"><MS><S N="Message">unknown shape</S></MS></Obj></Objs>` + "\n",
	} {
		if got := decodeCLIXMLStderr(in); got != in {
			t.Errorf("input %q was rewritten to %q", in, got)
		}
	}
}

// The streamed path sees stderr in arbitrary chunks; the filter must hold a
// partial line back, decode whole lines, and flush the tail.
func TestCLIXMLChunkFilter_DecodesAcrossChunkBoundaries(t *testing.T) {
	var out strings.Builder
	f := newCLIXMLChunkFilter(func(s string) { out.WriteString(s) })
	whole := clixmlErrorSample + "\r\ntrailing raw line"
	for i := 0; i < len(whole); i += 37 {
		end := i + 37
		if end > len(whole) {
			end = len(whole)
		}
		f.Write(whole[i:end])
	}
	f.Flush()
	got := out.String()
	if strings.Contains(got, "#< CLIXML") || strings.Contains(got, "<Objs") {
		t.Fatalf("CLIXML framing reached the stream:\n%s", got)
	}
	if !strings.Contains(got, " : cortexsim-boom\r\n") || !strings.HasSuffix(got, "trailing raw line") {
		t.Fatalf("decoded stream is wrong:\n%q", got)
	}
}
