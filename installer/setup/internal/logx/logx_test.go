package logx

import (
	"bytes"
	"strings"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
)

func TestLogger_RedactsRegisteredSecret(t *testing.T) {
	var buf bytes.Buffer
	l := New(&buf)
	l.Redact("s3cr3t-value")
	l.Line("x=%s", "s3cr3t-value")

	got := buf.String()
	if strings.Contains(got, "s3cr3t-value") {
		t.Fatalf("the registered secret reached the log: %q", got)
	}
	if !strings.HasSuffix(got, " x=[redacted]\n") {
		t.Errorf("line = %q, want it to end with %q", got, " x=[redacted]\n")
	}

	// A secret that contains another registered secret is redacted whole,
	// never left as "[redacted]-value".
	buf.Reset()
	l = New(&buf)
	l.Redact("s3cr3t")
	l.Redact("s3cr3t-value")
	l.Redact("") // ignored: an empty secret would redact between every byte
	l.Line("a=%s b=%s plain", "s3cr3t-value", "s3cr3t")
	if got := buf.String(); !strings.HasSuffix(got, " a=[redacted] b=[redacted] plain\n") {
		t.Errorf("line = %q, want both secrets redacted whole and the rest intact", got)
	}
}

func TestLogger_EventIsOneRedactedLine(t *testing.T) {
	var buf bytes.Buffer
	l := New(&buf)
	l.now = func() time.Time { return time.Date(2026, 11, 15, 7, 30, 0, 0, time.FixedZone("CST", -6*3600)) }
	l.Redact("tok-123")

	l.Event(engine.Event{StepID: "secret", Phase: "apply", Result: engine.Result{
		Code:   engine.CodeMasterKeyMismatch,
		Detail: "key tok-123 does not decrypt\nsecond line",
	}})
	l.Event(engine.Event{StepID: "ports", Phase: "check", Result: engine.Result{Code: engine.CodeOK}})

	want := "2026-11-15T13:30:00Z secret apply MASTER_KEY_MISMATCH key [redacted] does not decrypt\\nsecond line\n" +
		"2026-11-15T13:30:00Z ports check OK\n"
	if got := buf.String(); got != want {
		t.Errorf("log =\n%q\nwant\n%q", got, want)
	}
}
