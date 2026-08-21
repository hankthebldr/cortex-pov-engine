package identity

// Platform-aware identity resolution.
//
// The identity harness exists so a TTP step runs under a realistic service
// account and the endpoint sensor records a believable actor. On POSIX that is
// implemented with runuser/sudo/su. Windows has NO equivalent the beacon can
// use unattended: `runas` demands an interactive password prompt, and
// CreateProcessAsUser needs a token the beacon would have to obtain by other
// means. So on Windows a step declaring `identity: administrator` CANNOT be
// impersonated.
//
// The dangerous outcome is not that impersonation is unavailable — it is that
// the run record would still SAY the step ran as `administrator` while it
// actually ran as the beacon's own account. A POV readout asserting an
// attack executed under a privileged service account, when it did not, is a
// correctness lie the customer builds detection decisions on. Resolution
// therefore carries an explicit Degradation string that the beacon is required
// to write into the run record.
//
// The one case where the identity IS honoured on Windows is when the beacon is
// ALREADY running as the requested account (a beacon installed as a service
// under NT AUTHORITY\SYSTEM executing a step that asks for SYSTEM). That is
// checked against the real process token rather than assumed.

import (
	"fmt"
	"os/user"
	"runtime"
	"strings"
	"sync"
)

// Resolution is the outcome of mapping a scenario step's identity USERNAME to
// something this host can actually execute.
type Resolution struct {
	// Mode is the harness mode to execute with: direct|runuser|sudo_u|su.
	Mode string
	// Username is the account to impersonate; empty for direct mode.
	Username string
	// Unknown reports that the requested account is not in the canonical
	// service-account allowlist (best-effort impersonation; POSIX only).
	Unknown bool
	// Honoured reports whether the step will genuinely run under the requested
	// identity. False means the step still runs, but as somebody else.
	Honoured bool
	// Degradation is a human-readable explanation of an unhonoured identity,
	// written verbatim into the run record. Empty when Honoured is true.
	//
	// It is the ONLY signal that distinguishes "ran as www-data" from "claimed
	// www-data, ran as the beacon" — callers must not drop it.
	Degradation string
}

// Degraded reports whether this resolution failed to honour the requested
// identity and must therefore be surfaced to the operator.
func (r Resolution) Degraded() bool { return r.Degradation != "" }

// Resolve maps a requested identity username to a Resolution for THIS host.
func Resolve(requested string) Resolution {
	return ResolveFor(runtime.GOOS, requested, CurrentUsername())
}

// ResolveFor is the pure, host-independent form of Resolve. goos is a
// runtime.GOOS value and currentUser is the account the beacon itself runs as.
// Exposed separately so Windows behaviour is testable from a Linux CI runner —
// the only place this repo's Go suite executes.
func ResolveFor(goos, requested, currentUser string) Resolution {
	mode, user, unknown := ResolveIdentity(requested)

	if goos != "windows" {
		// POSIX: the harness can actually impersonate. An unknown account still
		// resolves to runuser and fails loudly at execution time (non-zero exit
		// with runuser's own diagnostic on stderr), which is already visible in
		// the run record.
		return Resolution{Mode: mode, Username: user, Unknown: unknown, Honoured: true}
	}

	// Windows from here down. Direct mode asks for no impersonation at all, so
	// running as the beacon account is exactly what was requested.
	if mode == "direct" {
		return Resolution{Mode: "direct", Honoured: true}
	}

	// The beacon may already BE the requested principal (e.g. installed as a
	// service under NT AUTHORITY\SYSTEM, running a step that asks for SYSTEM).
	// That is a genuinely honoured identity, not a degradation.
	if currentUser != "" && sameWindowsAccount(requested, currentUser) {
		return Resolution{Mode: "direct", Honoured: true}
	}

	who := currentUser
	if who == "" {
		who = "the beacon account"
	}
	return Resolution{
		Mode:     "direct",
		Unknown:  unknown,
		Honoured: false,
		Degradation: fmt.Sprintf(
			"identity %q could NOT be honoured on windows: the beacon has no unattended "+
				"impersonation primitive (runuser/sudo/su are POSIX-only; runas requires an "+
				"interactive password). The step executed as %s instead — endpoint telemetry "+
				"will attribute this activity to %s, NOT to %q. Treat any detection keyed on "+
				"the actor account as UNVALIDATED for this step.",
			requested, who, who, requested),
	}
}

// sameWindowsAccount compares a scenario's requested identity against the
// beacon's own account name.
//
// Windows account names arrive in several shapes for the same principal —
// `SYSTEM`, `NT AUTHORITY\SYSTEM`, `CONTOSO\svc-backup`, `svc-backup@contoso` —
// so the comparison is case-insensitive on the bare account name with any
// domain qualifier stripped from BOTH sides. This is deliberately conservative
// about what counts as a match: a false match here would re-introduce exactly
// the silent lie this file exists to prevent, so only the account name itself
// is compared, never a partial or prefix match.
func sameWindowsAccount(requested, current string) bool {
	return strings.EqualFold(bareAccountName(requested), bareAccountName(current))
}

func bareAccountName(s string) string {
	s = strings.TrimSpace(s)
	if i := strings.LastIndex(s, `\`); i >= 0 {
		s = s[i+1:]
	}
	if i := strings.Index(s, "@"); i > 0 {
		s = s[:i]
	}
	return s
}

var (
	currentUserOnce sync.Once
	currentUserName string
)

// CurrentUsername returns the account the beacon process runs as, or "" if it
// cannot be determined. Cached — the answer cannot change during a process's
// lifetime, and the lookup is a syscall on Windows.
func CurrentUsername() string {
	currentUserOnce.Do(func() {
		if u, err := user.Current(); err == nil {
			currentUserName = u.Username
		}
	})
	return currentUserName
}

// WrapCommandFor is WrapCommand with the host family passed explicitly.
//
// It is a defence-in-depth guard, not the primary control: ResolveFor already
// refuses to emit an impersonation mode on Windows. If a future caller
// hand-builds an ExecutionIdentity and bypasses resolution, this refuses rather
// than handing PowerShell a `runuser -l www-data -c ...` string that would fail
// at runtime with a confusing "term not recognized" and a plausible-looking
// non-zero exit.
func WrapCommandFor(goos string, identity ExecutionIdentity) (string, error) {
	if goos == "windows" {
		switch identity.Mode {
		case "", "direct":
		default:
			return "", fmt.Errorf(
				"identity mode %q is not executable on windows (runuser/sudo/su are POSIX-only); "+
					"resolve the step through identity.Resolve so the degradation is recorded",
				identity.Mode)
		}
	}
	return buildWrappedCommand(identity)
}
