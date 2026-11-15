// Package ealrelay runs a scenario step's EAL CLI call from a beacon.
//
// 71 scenario files call `python3 -m scripts.eal_simulator.cli ...` as an
// ordinary step. That CLI exists only inside SimCore, so on a target the step
// used to die with ModuleNotFoundError. For a step SimCore marked `eal_relay`
// the beacon swaps that one call for its own binary's `eal` subcommand:
//
//	step shell ── '<agent>' eal --relay 127.0.0.1:P --ticket T run c.yml --live
//	                   │  (this process: the step's child, the step's identity)
//	                   ▼ loopback
//	             the beacon ──► SimCore  POST /api/eal/relay
//	                   ▲                    runs collector steps itself, returns
//	                   │                    operations for network-shape steps
//	                   ▼
//	             prints SimCore's stdout, performs the operations, exits
//
// The subcommand reaches SimCore through the beacon that started the step, over
// loopback, with a ticket that is only good for that step while its task runs.
// So nothing long-lived is in the step's argv or environment, the subcommand
// needs no route or proxy of its own, and it works under a service account
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

// Invocation is what the rewritten call needs to find its way back.
type Invocation struct {
	Exe    string // this beacon's own binary
	Relay  string // the beacon's loopback listener, host:port
	Ticket string // good for one step of one task
}

// CallsCLI reports whether command contains the EAL CLI call.
func CallsCLI(command string) bool { return cliCall.MatchString(command) }

// Rewrite replaces every EAL CLI call in command with the beacon's `eal`
// subcommand and returns the new text and how many calls it replaced. Every
// other byte is left alone: the heredoc that writes the campaign, `$SRC_IP`,
// pipes into jq, `timeout --foreground 100 ...` in front of the call.
//
// goos and shell decide only how the binary's path is quoted: POSIX sh, or on
// Windows PowerShell (which needs the call operator in front of a quoted path)
// or cmd.
func Rewrite(command, goos, shell string, inv Invocation) (string, int) {
	n := len(cliCall.FindAllStringIndex(command, -1))
	if n == 0 {
		return command, 0
	}
	return cliCall.ReplaceAllLiteralString(command, invocationText(goos, shell, inv)), n
}

func invocationText(goos, shell string, inv Invocation) string {
	tail := " eal --relay " + inv.Relay + " --ticket " + inv.Ticket
	if goos != "windows" {
		return "'" + strings.ReplaceAll(inv.Exe, "'", `'\''`) + "'" + tail
	}
	if strings.EqualFold(strings.TrimSpace(shell), ShellCmd) {
		return `"` + inv.Exe + `"` + tail
	}
	return "& '" + strings.ReplaceAll(inv.Exe, "'", "''") + "'" + tail
}
