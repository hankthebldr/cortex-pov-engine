//go:build !windows

package executor

import (
	"context"
	"strings"
	"sync"
	"testing"
)

// A chained step hands its output to the callback line by line as the anchor
// prints it, and returns exactly what it returns without one.
func TestChainSession_StreamsLinesAndReturnsTheSameOutput(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	const cmd = "echo one; echo oops 1>&2; echo two; exit 4"

	plainOut, plainErr, plainRC, err := s.RunStepShell("step-01", "", cmd)
	if err != nil {
		t.Fatalf("plain step: %v", err)
	}

	var mu sync.Mutex
	var out, errs []string
	liveOut, liveErr, liveRC, err := s.RunStepShellStream("step-02", "", cmd, func(chunk string, isStderr bool) {
		mu.Lock()
		defer mu.Unlock()
		if isStderr {
			errs = append(errs, chunk)
		} else {
			out = append(out, chunk)
		}
	})
	if err != nil {
		t.Fatalf("streamed step: %v", err)
	}

	if liveOut != plainOut || liveErr != plainErr || liveRC != plainRC {
		t.Errorf("streamed step returned (%q, %q, %d), plain step (%q, %q, %d): they must match",
			liveOut, liveErr, liveRC, plainOut, plainErr, plainRC)
	}
	mu.Lock()
	defer mu.Unlock()
	if got := strings.Join(out, "|"); got != "one\n|two\n" {
		t.Errorf("stdout chunks = %q, want one line each", got)
	}
	if got := strings.Join(errs, "|"); got != "oops\n" {
		t.Errorf("stderr chunks = %q, want the one stderr line", got)
	}
	// Streamed text is the returned text: the blank line the anchor prints
	// before its sentinel is not the step's output.
	if got := strings.Join(out, ""); got != liveOut {
		t.Errorf("streamed stdout %q is not the returned stdout %q", got, liveOut)
	}
}

// Blank lines the step itself prints are streamed; only the anchor's own is not.
func TestChainSession_StreamKeepsTheStepsOwnBlankLines(t *testing.T) {
	s, err := NewChainSession(context.Background(), "")
	if err != nil {
		t.Fatalf("NewChainSession: %v", err)
	}
	defer s.Close()
	var mu sync.Mutex
	var live strings.Builder
	out, _, _, err := s.RunStepShellStream("step-01", "", "printf 'a\\n\\nb\\n\\n'", func(chunk string, isStderr bool) {
		mu.Lock()
		defer mu.Unlock()
		if !isStderr {
			live.WriteString(chunk)
		}
	})
	if err != nil {
		t.Fatalf("step: %v", err)
	}
	mu.Lock()
	defer mu.Unlock()
	if out != "a\n\nb\n\n" || live.String() != out {
		t.Errorf("returned %q, streamed %q, want both to be the four lines the step printed", out, live.String())
	}
}
