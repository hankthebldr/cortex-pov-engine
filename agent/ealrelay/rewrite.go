// Package ealrelay runs a scenario step's EAL CLI call from a beacon.
//
// 71 scenario files call `python3 -m scripts.eal_simulator.cli ...` as an
// ordinary step. That CLI exists only inside SimCore, so on a target the step
// used to die with ModuleNotFoundError. For a step SimCore marked `eal_relay`
// the beacon swaps that one call for its own binary's `eal` subcommand:
//
//	step shell ── '<agent>' eal --relay 127.0.0.1:P --ticket-file F run c.yml --live
//	                   │  (this process: the step's child, the step's identity)
//	                   ▼ loopback
//	             the beacon ──► SimCore  POST /api/eal/relay
//	                   ▲                    runs collector steps itself, returns
//	                   │                    operations for network-shape steps
//	                   ▼
//	             prints SimCore's stdout, performs the operations, exits
//
// The subcommand reaches SimCore through the beacon that started the step, over
// loopback. What lets it ask is a ticket, and the ticket is never on a command
// line or in an environment: the beacon writes it to a file only the step's
// account can read, and the command carries that file's path. The ticket is
// made when the step starts, is good for as many calls as the step's command
// contains, and is refused once the step has ended. So the subcommand needs no
// route, proxy or credential of its own, and it works under a service account
// whose environment `runuser -l` has reset.
//
// Stdlib only, like the rest of the beacon.
package ealrelay

import (
	"regexp"
	"strings"
)

// cliCall is the one call a marked step makes. core/engine/eal_relay.py holds
// the same expression (EAL_CLI_RE): SimCore marks a step with it, the beacon
// rewrites with it.
var cliCall = regexp.MustCompile(`\bpython3?(?:\.exe)?\s+-m\s+scripts\.eal_simulator\.cli\b`)

// Shells a step can run under on Windows (agent/executor names them the same).
const (
	ShellPowerShell = "powershell"
	ShellCmd        = "cmd"
)

// Invocation is what the rewritten call needs to find its way back. None of it
// is a secret: TicketFile is a path, and only the step's account can read what
// is at it.
type Invocation struct {
	Exe        string // this beacon's own binary
	Relay      string // the beacon's loopback listener, host:port
	TicketFile string // where the beacon puts this step's ticket
}

// CountCalls is how many EAL CLI calls command contains: the number of calls a
// step is allowed to make.
func CountCalls(command string) int {
	return len(cliCall.FindAllStringIndex(command, -1))
}

// Rewrite replaces every EAL CLI call in command with the beacon's `eal`
// subcommand and returns the new text and how many calls it replaced. Every
// other byte is left alone: the heredoc that writes the campaign, `$SRC_IP`,
// pipes into jq, `timeout --foreground 100 ...` in front of the call.
//
// goos and shell decide only how the two paths are quoted: POSIX sh, or on
// Windows PowerShell (which needs the call operator in front of a quoted path)
// or cmd.
func Rewrite(command, goos, shell string, inv Invocation) (string, int) {
	n := CountCalls(command)
	if n == 0 {
		return command, 0
	}
	return cliCall.ReplaceAllLiteralString(command, invocationText(goos, shell, inv)), n
}

func invocationText(goos, shell string, inv Invocation) string {
	quote := func(s string) string { return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'" }
	call := ""
	if goos == "windows" {
		if strings.EqualFold(strings.TrimSpace(shell), ShellCmd) {
			quote = func(s string) string { return `"` + s + `"` }
		} else {
			quote = func(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
			call = "& "
		}
	}
	return call + quote(inv.Exe) + " eal --relay " + inv.Relay + " --ticket-file " + quote(inv.TicketFile)
}
