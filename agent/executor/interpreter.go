// interpreter.go — runtime-interpreter detection, aliasing and a scoped PATH
// shim, plus package-manager detection for the opt-in "deliver a system
// update" path.
//
// # WHY THIS EXISTS
//
// A TTP step's command can depend on an interpreter it does not itself
// install (SIM-EDR-001 step-05 downloads the real mimipenguin.sh, which shells
// out to python). If that interpreter is simply absent, the beacon must never
// let the step's own command run and silently exit 0 — that is a manufactured
// false negative: the tool the step downloaded never actually executed, and
// the absent detections read in a POV report as "Cortex missed it".
//
// This file gives the beacon two honest options, matching the operator's
// directive verbatim: "either deliver system updates, or provide a python
// path into the local agent runtime environment":
//
//  1. PROVIDE A PATH — if a compatible interpreter exists under a different
//     name (e.g. only `python3` is installed, not the bare `python` some
//     tools hardcode), NewInterpreterShim exposes it under the requested
//     logical name via a throwaway directory prepended to ONE step's PATH.
//     Nothing on the host changes; the shim directory is removed the moment
//     the step finishes.
//  2. DELIVER SYSTEM UPDATES — InstallPackageCommand detects the host's
//     package manager and returns the install command for the package that
//     provides the requested interpreter. This is NEVER run unless the
//     operator explicitly authorised it for this run (Task.RuntimeInstallAuthorized) —
//     see client.go's resolveRuntimeDeps.
//
// Neither option requires the target to reach the public internet: option 1
// needs nothing beyond what is already on the box, and option 2 uses whatever
// package source the target's own package manager is already configured
// against (an internal mirror on a default-deny customer network, or nothing
// at all — in which case the install fails loudly, which is still honest).
//
// What this file deliberately does NOT do: stage a portable interpreter from
// the SimCore payload shelf. That would require the shelf to support archive
// artifacts (a full CPython distribution is a directory tree, not a single
// file), which docs/reference/payload-shelf.md documents as explicitly not
// yet supported by any consumer (§11 — `kind: archive` is rejected because no
// consumer, including this beacon's own Artifact struct, can unpack one). See
// docs/design/agent-runtime-dependencies.md for why that was scoped out of
// this pass.
package executor

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
)

// interpreterAliases lists, in preference order, the concrete binary names
// that satisfy a LOGICAL interpreter requirement. A scenario step declares the
// logical name ("python"); a real host frequently only has one of the aliases
// installed under a versioned name.
var interpreterAliases = map[string][]string{
	"python": {
		"python3", "python3.13", "python3.12", "python3.11", "python3.10",
		"python3.9", "python3.8", "python3.7", "python3.6",
		"python2.7", "python2", "python",
	},
	"perl": {"perl"},
	"ruby": {"ruby"},
	"node": {"node", "nodejs"},
}

// KnownLogicalInterpreters returns the sorted list of logical interpreter
// names this beacon knows how to reason about, for DetectInterpreters and for
// tests asserting the roster is what the design doc claims.
func KnownLogicalInterpreters() []string {
	out := make([]string, 0, len(interpreterAliases))
	for k := range interpreterAliases {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// InterpreterCheck is the outcome of resolving one logical interpreter name
// against the host's real PATH. It never installs or fabricates anything — it
// only reports what genuinely exists right now.
type InterpreterCheck struct {
	Logical  string
	Found    bool
	Exact    bool   // the literal logical name itself resolved (no shim needed)
	Resolved string // the concrete binary name that was found (e.g. "python3")
	Path     string // absolute path to that binary
}

// ResolveInterpreter looks for `logical` on PATH, first under its own name and
// then under its known aliases (preference order in interpreterAliases). An
// unknown logical name with no alias table entry still checks its own literal
// name — this keeps the function usable for a name the alias table hasn't
// been taught about yet.
func ResolveInterpreter(logical string) InterpreterCheck {
	if p, err := exec.LookPath(logical); err == nil {
		return InterpreterCheck{Logical: logical, Found: true, Exact: true, Resolved: logical, Path: p}
	}
	for _, alias := range interpreterAliases[logical] {
		if alias == logical {
			continue
		}
		if p, err := exec.LookPath(alias); err == nil {
			return InterpreterCheck{Logical: logical, Found: true, Exact: false, Resolved: alias, Path: p}
		}
	}
	return InterpreterCheck{Logical: logical}
}

// DetectInterpreters probes the fixed logical-interpreter roster this beacon
// knows how to reason about and returns which ones are available (exact or
// aliased). This is advertised to SimCore at registration time so the
// orchestrator can preflight a scenario's declared `requires_interpreters`
// against the REAL target instead of discovering the gap mid-run.
func DetectInterpreters() []InterpreterCheck {
	logicals := KnownLogicalInterpreters()
	out := make([]InterpreterCheck, 0, len(logicals))
	for _, l := range logicals {
		out = append(out, ResolveInterpreter(l))
	}
	return out
}

// AvailableLogicalNames filters DetectInterpreters down to the logical names
// that resolved to something (exact or aliased) — the shape SimCore's agent
// registration record actually stores.
func AvailableLogicalNames() []string {
	var out []string
	for _, c := range DetectInterpreters() {
		if c.Found {
			out = append(out, c.Logical)
		}
	}
	return out
}

// InterpreterShim is a scratch directory containing a single symlink named
// exactly the logical interpreter name (e.g. "python"), pointing at a
// concrete binary found under a different name (e.g. "/usr/bin/python3"). A
// subprocess with this directory PREPENDED to its PATH sees a binary literally
// called "python" even though the host only shipped "python3". This changes
// NOTHING on the host: the directory lives under the OS temp dir, is visible
// only to the one subprocess whose PATH it is prepended to, and Cleanup
// removes it the moment the step finishes.
type InterpreterShim struct {
	Dir string
}

// NewInterpreterShim creates the scratch directory and symlink described above.
func NewInterpreterShim(logical, targetPath string) (*InterpreterShim, error) {
	dir, err := os.MkdirTemp("", "cortexsim-interp-shim-")
	if err != nil {
		return nil, fmt.Errorf("interpreter shim mkdir: %w", err)
	}
	// I3: os.MkdirTemp creates the directory 0700, owned by whichever euid the
	// beacon process runs as (root — the systemd unit declares no `User=`).
	// The step this shim is FOR is very often executed as a different,
	// unprivileged identity (runuser -l 'www-data' -c '...'): that identity
	// cannot traverse a 0700 root-owned directory to reach the shim, so the
	// wrapped command fails to resolve the shimmed binary even though the
	// step's own output claims "PATH-shimmed". 0755 makes the directory
	// traversable and listable by anyone while the symlink inside it still
	// only points at a binary that was already resolvable on this host — this
	// does not widen what the target identity can execute, only whether it
	// can see the shim.
	if err := os.Chmod(dir, 0o755); err != nil {
		_ = os.RemoveAll(dir)
		return nil, fmt.Errorf("interpreter shim chmod: %w", err)
	}
	link := filepath.Join(dir, logical)
	if err := os.Symlink(targetPath, link); err != nil {
		_ = os.RemoveAll(dir)
		return nil, fmt.Errorf("interpreter shim symlink: %w", err)
	}
	return &InterpreterShim{Dir: dir}, nil
}

// Cleanup removes the shim directory. Safe to call on a nil receiver or more
// than once.
func (s *InterpreterShim) Cleanup() {
	if s == nil || s.Dir == "" {
		return
	}
	_ = os.RemoveAll(s.Dir)
	s.Dir = ""
}

// packageManagers is checked in order; the first one found on PATH is used.
// The install command is intentionally quiet/non-interactive so it does not
// hang a headless pull-mode run waiting on a prompt.
var packageManagers = []struct {
	bin        string
	installFmt string // %s = package name
}{
	{"apt-get", "DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq %s"},
	{"dnf", "dnf install -y -q %s"},
	{"yum", "yum install -y -q %s"},
	{"apk", "apk add --no-cache %s"},
}

// interpreterPackage maps a logical interpreter name to the package that
// provides it. Deliberately small and explicit — an approximate guess here
// would silently "fix" the wrong thing on a customer host under the same
// consent that authorised installing something narrow and named.
var interpreterPackage = map[string]string{
	"python": "python3",
	"perl":   "perl",
	"ruby":   "ruby",
	"node":   "nodejs",
}

// InstallPackageCommand returns the shell command that would install the
// package providing `logical`, and whether a supported package manager and a
// known package mapping both exist on THIS host. It never runs the command —
// the caller decides whether the operator has authorised that (Task.RuntimeInstallAuthorized).
func InstallPackageCommand(logical string) (string, bool) {
	pkg, ok := interpreterPackage[logical]
	if !ok {
		return "", false
	}
	for _, pm := range packageManagers {
		if _, err := exec.LookPath(pm.bin); err == nil {
			return fmt.Sprintf(pm.installFmt, pkg), true
		}
	}
	return "", false
}

// postInstallDependencyMissingMarker MUST stay byte-identical to
// runtimeDepMissingMarker in agent/beacon/client.go. It cannot be a shared
// import (beacon imports executor, not the reverse, and this package must
// not depend on beacon), so it is a literal here — same reasoning as
// identityDegradedMarker: it is what an operator, and any downstream reader
// such as deploy/tier-d/classify.py, greps for.
const postInstallDependencyMissingMarker = "!! RUNTIME_DEPENDENCY_MISSING:"

// PostInstallVerifyShim returns a POSIX-sh snippet that re-resolves `logical`
// AFTER an authorized package install has (allegedly) supplied it, and, if it
// only resolved under an alias, creates the same kind of PATH shim
// NewInterpreterShim would have created had the alias been there up front.
//
// # C2 — WHY THIS EXISTS
//
// Before this fix, the authorized-install branch composed
// "apt-get install -y -qq python3 && (<original command>)" and stopped: it
// never checked what the install actually put on PATH. `apt-get install
// python3` produces /usr/bin/python3 and NO /usr/bin/python; a step whose own
// command hardcodes the bare name `python` (the overwhelming majority of this
// corpus's declared `requires_interpreters: [python]` steps) would then fail
// to resolve it, and — because a great many of those commands carry their own
// `|| echo ...` fallback (the exact SIM-EDR-001 pattern this whole feature
// exists to stop masking) — the step reported exit 0 with NO signal. The
// operator's own authorization reintroduced the defect the feature exists to
// close.
//
// The two branches of resolveRuntimeDeps must converge on the SAME guarantee:
// "found only as an alias" always gets a PATH shim, whether the alias was
// there before the step started or arrived because THIS step's own install
// just put it there. This function is the second branch's half of that
// convergence, expressed as shell text because — unlike the pre-existing
// path — the install itself only happens once the composed command actually
// runs on the target (this beacon does not run apt-get itself; it hands the
// identity-wrapped shell the whole "install && verify && run" pipeline).
//
// On failure this prints the same stable marker resolveRuntimeDeps uses for
// the "never found at all" case and exits 127 — an install that reports
// success but leaves the target without a working interpreter is exactly as
// unproven as an install that was never attempted.
func PostInstallVerifyShim(logical string) string {
	quoted := shellSingleQuote(logical)
	var b strings.Builder
	fmt.Fprintf(&b, "if ! command -v %s >/dev/null 2>&1; then\n", quoted)
	b.WriteString("  __cortexsim_found=\"\"\n")
	for _, alias := range interpreterAliases[logical] {
		if alias == logical {
			continue
		}
		aq := shellSingleQuote(alias)
		fmt.Fprintf(&b, "  if [ -z \"$__cortexsim_found\" ] && command -v %s >/dev/null 2>&1; then __cortexsim_found=%s; fi\n", aq, aq)
	}
	b.WriteString("  if [ -z \"$__cortexsim_found\" ]; then\n")
	fmt.Fprintf(&b, "    echo %s >&2\n",
		shellSingleQuote(fmt.Sprintf(
			"%s %s (post-install verification failed — the install reported success but no compatible interpreter is on PATH)",
			postInstallDependencyMissingMarker, logical)))
	b.WriteString("    exit 127\n")
	b.WriteString("  fi\n")
	b.WriteString("  __cortexsim_shimdir=\"$(mktemp -d)\"\n")
	// Mirrors NewInterpreterShim's I3 fix: a root-run beacon's mktemp -d is
	// 0700 by default, which a non-root step identity (runuser -l www-data)
	// could not traverse.
	b.WriteString("  chmod 0755 \"$__cortexsim_shimdir\"\n")
	fmt.Fprintf(&b, "  ln -s \"$(command -v \"$__cortexsim_found\")\" \"$__cortexsim_shimdir/%s\"\n", logical)
	b.WriteString("  export PATH=\"$__cortexsim_shimdir:$PATH\"\n")
	b.WriteString("fi")
	return b.String()
}

// shellSingleQuote wraps s in POSIX-sh single quotes, escaping any embedded
// single quote the '\”  way. Every caller here passes a logical/alias name
// from this file's own literal tables (interpreterAliases /
// postInstallDependencyMissingMarker text), so this is defense in depth, not
// a response to untrusted input.
func shellSingleQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", `'\''`) + "'"
}
