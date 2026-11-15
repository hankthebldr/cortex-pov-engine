package executor

// CLIXML on stderr.
//
// Windows PowerShell 5.1 started with -EncodedCommand, non-interactive, with
// stderr redirected, serialises everything it writes to the error stream as
// CLIXML: a "#< CLIXML" header line followed by an <Objs> document whose
// <S S="Error"> strings are the lines of the formatted error. It does this
// whether or not -OutputFormat Text is passed (PowerShell 7 honours the flag;
// 5.1 does not for an encoded command). Observed on the first two runs of the
// windows-2022 CI job, 2026-11-10: a failing cmdlet's error reached the run
// record as an XML envelope instead of text.
//
// The beacon keeps -EncodedCommand (it is what keeps a TTP's quoting intact and
// what the process telemetry of every Windows step looks like), so the step's
// stderr is decoded here instead, after capture. Only the PowerShell envelope is
// touched. Bytes that are not that envelope pass through unchanged, and an
// envelope that cannot be read is left as it was: a step's output is evidence,
// and a shape this code does not recognise must stay visible, never vanish.

import (
	"html"
	"regexp"
	"strconv"
	"strings"
)

var (
	// One complete PowerShell CLIXML document, with the line ending that
	// follows it. The namespace is what makes it PowerShell's.
	clixmlDocRe = regexp.MustCompile(`(?s)<Objs [^>]*xmlns="http://schemas\.microsoft\.com/powershell/2004/04"[^>]*>(.*?)</Objs>(\r?\n)?`)
	// A stream-tagged string inside a document: Error, Warning, Verbose, Debug...
	clixmlStringRe = regexp.MustCompile(`(?s)<S S="[A-Za-z]+">(.*?)</S>`)
	// A progress record: not an error, and suppressed at source by the wrapper.
	clixmlProgressRe = regexp.MustCompile(`(?s)<Obj S="progress"[^>]*>.*?</Obj>`)
	// The header PowerShell writes once, on a line of its own.
	clixmlHeaderRe = regexp.MustCompile(`(?m)^#< CLIXML\r?(\n|$)`)
	// CLIXML's escape for a UTF-16 code unit; a literal "_x" is "_x005F_x".
	clixmlEscapeRe = regexp.MustCompile(`_x([0-9A-Fa-f]{4})_`)
)

// decodeCLIXMLStderr rewrites PowerShell CLIXML found in a step's stderr into
// the text it carries. Progress records are dropped; they are not errors. A
// document holding anything other than stream strings and progress records is
// a shape this code has not seen: it is left exactly as it was, header and all.
func decodeCLIXMLStderr(s string) string {
	if !strings.Contains(s, "#< CLIXML") && !strings.Contains(s, "<Objs ") {
		return s
	}
	leftRaw := false
	out := clixmlDocRe.ReplaceAllStringFunc(s, func(doc string) string {
		m := clixmlDocRe.FindStringSubmatch(doc)
		rest := clixmlStringRe.ReplaceAllString(m[1], "")
		rest = clixmlProgressRe.ReplaceAllString(rest, "")
		if strings.TrimSpace(rest) != "" {
			leftRaw = true
			return doc
		}
		var b strings.Builder
		for _, sm := range clixmlStringRe.FindAllStringSubmatch(m[1], -1) {
			b.WriteString(clixmlUnescape(sm[1]))
		}
		text := b.String()
		// Keep the document's own line ending only when the text needs one.
		if text != "" && !strings.HasSuffix(text, "\n") {
			text += m[2]
		}
		return text
	})
	if leftRaw {
		return out
	}
	// With every document decoded, the header line is only framing.
	return clixmlHeaderRe.ReplaceAllString(out, "")
}

func clixmlUnescape(s string) string {
	s = html.UnescapeString(s)
	return clixmlEscapeRe.ReplaceAllStringFunc(s, func(esc string) string {
		n, err := strconv.ParseUint(esc[2:6], 16, 32)
		if err != nil {
			return esc
		}
		return string(rune(n))
	})
}

// clixmlChunkFilter decodes a live stderr stream. Chunks arrive at arbitrary
// boundaries, so it holds back the unfinished last line, decodes each complete
// line, and Flush emits whatever is left when the step ends.
type clixmlChunkFilter struct {
	emit    func(string)
	pending string
	sawDoc  bool
}

func newCLIXMLChunkFilter(emit func(string)) *clixmlChunkFilter {
	return &clixmlChunkFilter{emit: emit}
}

func (f *clixmlChunkFilter) Write(chunk string) {
	f.pending += chunk
	i := strings.LastIndexByte(f.pending, '\n')
	if i < 0 {
		return
	}
	ready := f.pending[:i+1]
	f.pending = f.pending[i+1:]
	f.send(ready)
}

// Flush emits the held-back tail. Call it once, after the process has exited.
func (f *clixmlChunkFilter) Flush() {
	if f.pending == "" {
		return
	}
	tail := f.pending
	f.pending = ""
	f.send(tail)
}

func (f *clixmlChunkFilter) send(text string) {
	text = decodeCLIXMLStderr(text)
	if text != "" {
		f.emit(text)
	}
}
