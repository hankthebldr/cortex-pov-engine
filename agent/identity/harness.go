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
	"context"
	"fmt"
	"runtime"
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

// WrapCommand returns the exact shell string this harness would hand to the
// executor for the given identity — the same string RunCommandCtx runs via
// `sh -c`. Exposed so the causality-chained executor (a shared anchor shell)
// can run the identically-wrapped command as a child of the anchor, keeping
// identity-harness semantics byte-identical across the per-step and chained
// execution paths.
// It routes through WrapCommandFor so a mode this host cannot execute is
// refused rather than handed to the shell (see platform.go).
func WrapCommand(identity ExecutionIdentity) (string, error) {
	return WrapCommandFor(runtime.GOOS, identity)
}

// Execute runs the command described by identity under the appropriate identity context.
// It always goes through this harness — even "direct" mode — for consistent logging and
// result capture (per spec constraint §10 rule 5).
//
// Execute is a thin wrapper around ExecuteCtx with a background context, preserved
// for callers that do not need cancellation.
func Execute(identity ExecutionIdentity) (ExecResult, error) {
	return ExecuteCtx(context.Background(), identity)
}

// ExecuteCtxStream is the context-aware streaming variant of Execute. It streams
// stdout and stderr chunks in real-time as they are written.
func ExecuteCtxStream(ctx context.Context, identity ExecutionIdentity, onChunk executor.OutputChunkFunc) (ExecResult, error) {
	wrapped, err := WrapCommandFor(runtime.GOOS, identity)
	if err != nil {
		return ExecResult{}, err
	}

	start := time.Now()
	stdout, stderr, exitCode, execErr := executor.RunCommandCtxStream(ctx, wrapped, onChunk)
	duration := time.Since(start)

	if execErr != nil {
		// context.Canceled is propagated verbatim so the caller can distinguish an
		// operator abort from a genuine execution error; both carry captured output.
		if execErr == context.Canceled {
			return ExecResult{
				ExitCode: exitCode,
				Stdout:   stdout,
				Stderr:   stderr,
				Duration: duration,
			}, execErr
		}
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

// ExecuteCtx is the context-aware variant of Execute. It honours ctx for
// cancellation: when ctx is cancelled the underlying process group is signalled
// (SIGTERM → SIGKILL) so an operator abort can stop a long-running step. A
// cancelled run returns an ExecResult with ExitCode 130 and err == context.Canceled,
// which the caller should treat as a non-fatal "aborted" outcome rather than an
// execution failure.
func ExecuteCtx(ctx context.Context, identity ExecutionIdentity) (ExecResult, error) {
	return ExecuteCtxStream(ctx, identity, nil)
}

// serviceAccounts is the allowlist of known service accounts that should be
// impersonated via runuser. It MUST stay in sync with the canonical
// identity-harness spec at spec/identity_harness.json — the SINGLE source of
// truth shared with the push-mode bash generator (core/engine/push_generator.py
// via core/engine/identity_spec.py). harness_spec_test.go asserts this map
// matches the spec so push and pull resolve a scenario's identity identically.
var serviceAccounts = map[string]bool{
	"www-data":     true,
	"postgres":     true,
	"mysql":        true,
	"node":         true,
	"python3":      true,
	"nobody":       true,
	"svc-backup":   true,
	"app":          true,
	"developer":    true,
	"runner":       true,
	"vertex-agent": true,
}

// ResolveIdentity maps a scenario step's identity USERNAME string to a harness
// {mode, username} pair. It mirrors the core/engine/push_generator.py run_as()
// allowlist:
//
//	{root, container-runtime, direct, ""}                       → direct  (username ignored)
//	{www-data, postgres, mysql, node, python3, nobody, svc-backup} → runuser
//	any other non-empty username                                → runuser (best-effort; unknown=true)
//
// Unknown usernames resolve to runuser with unknown=true so the caller can emit a
// WARN — matching the spirit of the bash harness's "Unknown identity" warning.
// The Go runuser path degrades to a clean stderr failure if the user is absent.
func ResolveIdentity(username string) (mode, user string, unknown bool) {
	switch username {
	case "", "root", "container-runtime", "direct":
		return "direct", "", false
	}
	if serviceAccounts[username] {
		return "runuser", username, false
	}
	return "runuser", username, true
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

	case "sudo_sh":
		// sudo -n -u <username> /bin/sh -c "<command>" — the macOS arm.
		//
		// Darwin has no runuser (util-linux) and its BSD su takes no -s flag,
		// so neither of the two modes above exists there. sudo does, and
		// wrapping in `/bin/sh -c` keeps the command's shell semantics, which
		// sudo_u's whitespace split would destroy for the pipelines and && in
		// this corpus.
		//
		// -n is load-bearing: without it, a beacon NOT running as root gets an
		// interactive password prompt on a headless host and the step hangs
		// until the step timeout kills it, reporting exit 124 with no
		// diagnostic. With -n sudo exits immediately saying a password is
		// required, which names the real problem.
		return fmt.Sprintf("sudo -n -u %s /bin/sh -c %s",
			shellQuote(identity.Username),
			shellQuote(identity.Command),
		), nil

	default:
		return "", fmt.Errorf("unknown identity mode: %q (must be direct|runuser|sudo_u|su|sudo_sh)", identity.Mode)
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
