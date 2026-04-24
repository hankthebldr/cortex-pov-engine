// Package identity wraps every TTP command execution in an identity context,
// producing realistic process-causality chains in XSIAM/XDR telemetry.
//
// Supported modes (Section 7 of CORTEXSIM_AGENT_CONTEXT.md):
//
//	direct   — run the command as-is (no impersonation wrapper)
//	runuser  — runuser -l <username> -c "<command>"
//	sudo_u   — sudo -u <username> <args...>   (command split, not wrapped in -c)
//	su       — su -s /bin/bash <username> -c "<command>"
package identity

import (
	"fmt"
	"strings"
	"time"

	"github.com/hankthebldr/cortexsim/agent/executor"
)

// ExecutionIdentity describes how a TTP command should be executed.
type ExecutionIdentity struct {
	// Mode is one of: "direct", "runuser", "sudo_u", "su"
	Mode string
	// Username is the service account to impersonate (e.g. "www-data", "postgres", "nobody").
	// Ignored when Mode is "direct".
	Username string
	// Command is the TTP shell command to execute.
	Command string
}

// ExecResult holds the outcome of a single command execution.
type ExecResult struct {
	ExitCode int
	Stdout   string
	Stderr   string
	Duration time.Duration
}

// Execute runs the command described by identity under the appropriate identity context.
// It always goes through this harness — even "direct" mode — for consistent logging and
// result capture (per spec constraint §10 rule 5).
func Execute(identity ExecutionIdentity) (ExecResult, error) {
	wrapped, err := buildWrappedCommand(identity)
	if err != nil {
		return ExecResult{}, err
	}

	start := time.Now()
	stdout, stderr, exitCode, execErr := executor.RunCommand(wrapped)
	duration := time.Since(start)

	if execErr != nil {
		return ExecResult{
			ExitCode: exitCode,
			Stdout:   stdout,
			Stderr:   stderr,
			Duration: duration,
		}, fmt.Errorf("identity.Execute [mode=%s user=%s]: %w", identity.Mode, identity.Username, execErr)
	}

	return ExecResult{
		ExitCode: exitCode,
		Stdout:   stdout,
		Stderr:   stderr,
		Duration: duration,
	}, nil
}

// buildWrappedCommand constructs the final shell string based on the identity mode.
func buildWrappedCommand(identity ExecutionIdentity) (string, error) {
	switch identity.Mode {
	case "direct", "":
		// Run exactly as given — no wrapper.
		return identity.Command, nil

	case "runuser":
		// runuser -l <username> -c "<command>"
		// The command is passed as a single string argument to -c.
		return fmt.Sprintf("runuser -l %s -c %s",
			shellQuote(identity.Username),
			shellQuote(identity.Command),
		), nil

	case "sudo_u":
		// sudo -u <username> <args...>
		// The command is split into individual arguments rather than wrapped in -c,
		// which produces a cleaner causality chain in the process tree.
		parts := splitCommand(identity.Command)
		if len(parts) == 0 {
			return "", fmt.Errorf("sudo_u mode: command is empty")
		}
		quotedParts := make([]string, len(parts))
		for i, p := range parts {
			quotedParts[i] = shellQuote(p)
		}
		return fmt.Sprintf("sudo -u %s %s",
			shellQuote(identity.Username),
			strings.Join(quotedParts, " "),
		), nil

	case "su":
		// su -s /bin/bash <username> -c "<command>"
		return fmt.Sprintf("su -s /bin/bash %s -c %s",
			shellQuote(identity.Username),
			shellQuote(identity.Command),
		), nil

	default:
		return "", fmt.Errorf("unknown identity mode: %q (must be direct|runuser|sudo_u|su)", identity.Mode)
	}
}

// shellQuote wraps s in single quotes and escapes any embedded single quotes.
// This is safe for POSIX /bin/sh.
func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'"
}

// splitCommand splits a shell command string into tokens on whitespace.
// This is intentionally simple — for sudo_u the command should be a single
// binary invocation; complex pipelines should use "direct" or "su" mode instead.
func splitCommand(cmd string) []string {
	return strings.Fields(cmd)
}
