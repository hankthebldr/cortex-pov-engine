// Package logx writes the setup log: one line per step result, every line
// stamped in UTC, and every registered secret replaced before a byte reaches
// the writer. Secrets should never be handed to the logger at all; Redact is
// the second line of defence for the one that slips through.
package logx

import (
	"fmt"
	"io"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
)

const redacted = "[redacted]"

// Logger is safe for concurrent use: the wizard server logs from its own
// goroutine while the engine runs.
type Logger struct {
	mu      sync.Mutex
	w       io.Writer
	secrets []string // longest first, so a secret containing another is replaced whole
	now     func() time.Time
}

// New returns a Logger that writes to w. Write errors are dropped: a full disk
// must not turn a working install into a failed one.
func New(w io.Writer) *Logger {
	return &Logger{w: w, now: time.Now}
}

// Redact registers a value that must never appear in the log. Every later
// line has each occurrence replaced with "[redacted]". An empty value is
// ignored.
func (l *Logger) Redact(secret string) {
	if secret == "" {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	for _, s := range l.secrets {
		if s == secret {
			return
		}
	}
	l.secrets = append(l.secrets, secret)
	sort.SliceStable(l.secrets, func(i, j int) bool { return len(l.secrets[i]) > len(l.secrets[j]) })
}

// Event writes one line: "<RFC3339 UTC time> <step-id> <phase> <code> <detail>".
// Line breaks in the detail are written as a literal \n so the event stays on
// one line.
func (l *Logger) Event(e engine.Event) {
	msg := e.StepID + " " + e.Phase + " " + string(e.Result.Code)
	if e.Result.Detail != "" {
		msg += " " + e.Result.Detail
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	msg = strings.NewReplacer("\r\n", `\n`, "\n", `\n`, "\r", `\n`).Replace(l.redact(msg))
	l.write(msg)
}

// Line writes a free-form line, formatted as fmt.Sprintf does, after the
// timestamp.
func (l *Logger) Line(format string, args ...any) {
	msg := fmt.Sprintf(format, args...)
	l.mu.Lock()
	defer l.mu.Unlock()
	l.write(strings.TrimRight(l.redact(msg), "\n"))
}

// redact and write are called with l.mu held.
func (l *Logger) redact(s string) string {
	for _, secret := range l.secrets {
		s = strings.ReplaceAll(s, secret, redacted)
	}
	return s
}

func (l *Logger) write(msg string) {
	_, _ = io.WriteString(l.w, l.now().UTC().Format(time.RFC3339)+" "+msg+"\n")
}
