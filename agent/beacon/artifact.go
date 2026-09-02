package beacon

// Artifact staging — the beacon half of the payload shelf.
//
// The console pulls a public tool onto the DC's own SimCore, composes a
// digest-bound plan, and the plan travels in the task. This file fetches those
// bytes onto the target, verifies them against the digest THE TASK CARRIED IN,
// and only then puts a runnable file on disk.
//
// Everything here exists because of one sentence: a step whose tool could not be
// staged must not run, must not be skipped silently, and must be reported as
// not-having-run. A beacon that runs the TTP without its tool produces "the TTP
// executed and Cortex detected nothing" — a manufactured false negative, printed
// in a POV report, shown to a customer. That is worse than any crash.
//
// Stdlib only, by contract: net/http + crypto/sha256 + os. That is what keeps
// the beacon a ~5 MB static binary that installs on an air-gapped host.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// -------------------------------------------------------------------------
// The wire contract
// -------------------------------------------------------------------------

// Artifact is one staged tool the task requires BEFORE any step runs.
//
// The sha256 is resolved on the DC's own SimCore at enqueue time and travels in
// the task, so the beacon verifies against a value it carried in rather than one
// it fetched from the same server it is trusting — the identical anchoring the
// k8s manifest uses. A blank digest is a hard refusal, never a skipped check:
// comparing against "" is how an integrity check silently becomes a no-op.
type Artifact struct {
	Name      string   `json:"name"`
	SHA256    string   `json:"sha256"`
	SizeBytes int64    `json:"size_bytes,omitempty"`
	Path      string   `json:"path"` // SERVER-RELATIVE, joined onto our own ServerURL
	Dest      string   `json:"dest"` // absolute, computed server-side
	Mode      string   `json:"mode,omitempty"`
	Steps     []string `json:"steps,omitempty"`
}

// -------------------------------------------------------------------------
// Failure taxonomy
// -------------------------------------------------------------------------
//
// The codes are distinct because the FIXER is a different person in each row.
// This is the lesson SIMCORE_UNREACHABLE vs BOOTSTRAP_FETCH_FAILED already
// encodes in the k8s bootstrap: collapsing them sends a DC to argue with the
// customer's network team about a problem that is not theirs.

const (
	codeSpecInvalid    = "ARTIFACT_SPEC_INVALID"    // server/content bug
	codeDestRefused    = "ARTIFACT_DEST_REFUSED"    // content: dest not stageable here
	codeUnreachable    = "SIMCORE_UNREACHABLE"      // network
	codeUnavailable    = "ARTIFACT_UNAVAILABLE"     // operator: re-run build-payloads.sh
	codeForbidden      = "ARTIFACT_FORBIDDEN"       // operator: token auth is on
	codeTruncated      = "ARTIFACT_TRUNCATED"       // network / intercepting proxy
	codeTooLarge       = "ARTIFACT_TOO_LARGE"       // server: bad shelf entry
	codeSHA256Mismatch = "ARTIFACT_SHA256_MISMATCH" // SECURITY
	codeWriteFailed    = "ARTIFACT_WRITE_FAILED"    // host
	codeDestNoexec     = "ARTIFACT_DEST_NOEXEC"     // host: noexec mount
)

// Stable literal markers. They are constants for the same reason
// identityDegradedMarker is: an operator — and any downstream parser of the run
// record or the POV report — greps for them.
const (
	artifactNotStagedMarker     = "!! ARTIFACT NOT STAGED:"
	artifactStagingFailedMarker = "ARTIFACT STAGING FAILED:"
	artifactResidueMarker       = "!! ARTIFACT RESIDUE:"
)

// artifactStagingExitCode is EX_CONFIG. It deliberately matches the k8s pod's
// "this never ran" code so a DC learns ONE exit code across both delivery
// vehicles.
const artifactStagingExitCode = 78

// These three are vars, not consts, ONLY so the test suite can shrink them.
// Nothing in the shipped binary reassigns them; a 512 MB cap and a 6-second
// retry ladder cannot otherwise be exercised in CI, and an untested retry ladder
// is how "never retry a digest mismatch" quietly stops being true.
var (
	// maxArtifactBytes caps a download regardless of the advertised size_bytes:
	// a broken or hostile server must not be able to fill a customer's /tmp.
	maxArtifactBytes int64 = 512 << 20
	// artifactFetchTimeout bounds ONE artifact. It is not a client-wide
	// timeout — a single 30s client deadline cannot express "60s to connect,
	// 2min to stream 200MB".
	artifactFetchTimeout = 2 * time.Minute
	artifactRetryBase    = 2 * time.Second
)

const (
	artifactRetries    = 3
	stalePartSuffix    = ".cortexsim-part"
	execProbeName      = ".cs-exec-probe"
	staleStagingMaxAge = 24 * time.Hour
)

// artifactFailure is one artifact that could not be staged.
type artifactFailure struct {
	Artifact Artifact
	Code     string
	Detail   string
}

// -------------------------------------------------------------------------
// stagedSet — what landed, what did not, and what must be removed
// -------------------------------------------------------------------------

type stagedFile struct {
	Artifact  Artifact
	Dest      string
	SHA256    string
	Size      int64
	FromCache bool
}

// stagedSet is the ledger of one task's staging phase. It is returned even on
// failure, because Cleanup must remove whatever DID land before the failure.
type stagedSet struct {
	Server   string
	Total    int
	Files    []stagedFile
	Failures []artifactFailure

	// dirs we created, outermost-first in creation order. Cleanup removes them
	// innermost-first with os.Remove (empty-only) — never RemoveAll, so a path
	// we did not write is never touched.
	dirs []string
	// exec probes written during pre-flight, removed by Cleanup.
	probes []string
}

// OK reports whether every declared artifact is on disk and verified.
func (s *stagedSet) OK() bool { return s != nil && len(s.Failures) == 0 }

// Ledger renders the success frame. It rhymes with the pod's
// "[cortexsim] staged tool …" line on purpose: one vocabulary, two consumers.
func (s *stagedSet) Ledger() string {
	var b strings.Builder
	fmt.Fprintf(&b, "=== ARTIFACT STAGING · %d artifact(s) ===\n", s.Total)
	for _, f := range s.Files {
		mode := f.Artifact.Mode
		if mode == "" {
			mode = defaultArtifactMode
		}
		cached := ""
		if f.FromCache {
			cached = " (cache-hit)"
		}
		fmt.Fprintf(&b, "[cortexsim] staged %s sha256=%s %dB%s -> %s mode=%s\n",
			f.Artifact.Name, shortDigest(f.SHA256), f.Size, cached, f.Dest, mode)
	}
	fmt.Fprintf(&b, "[cortexsim] source=%s · digests carried in the task, resolved on SimCore at enqueue time\n", s.Server)
	// Trailing newline: /output frames are concatenated verbatim into run.output,
	// so a frame without one runs into the next step's header.
	b.WriteString("--- staging ok ---\n")
	return b.String()
}

func shortDigest(d string) string {
	if len(d) <= 12 {
		return d
	}
	return d[:12] + "…"
}

// -------------------------------------------------------------------------
// Staging
// -------------------------------------------------------------------------

const defaultArtifactMode = "0755"

// stageArtifacts fetches and verifies every artifact the task declares, BEFORE
// any step runs. All-or-nothing: a per-step lazy fetch that fails on step 4
// leaves a half-executed scenario and a report a reader must parse carefully to
// know which half is evidence.
func (c *BeaconClient) stageArtifacts(ctx context.Context, task *Task) *stagedSet {
	set := &stagedSet{Server: c.ServerURL, Total: len(task.Artifacts)}
	if len(task.Artifacts) == 0 {
		return set
	}

	log.Printf("[beacon] staging %d artifact(s) for run_id=%s before any step runs",
		len(task.Artifacts), task.RunID)

	for _, a := range task.Artifacts {
		if fail := c.stageOne(ctx, a, set); fail != nil {
			set.Failures = append(set.Failures, *fail)
			// Keep going: naming EVERY artifact that failed in one report beats
			// making an operator re-run to discover the second problem.
			continue
		}
	}

	if len(set.Failures) == 0 {
		if fail := c.probeExecutability(set); fail != nil {
			set.Failures = append(set.Failures, *fail)
		}
	}
	return set
}

// stageOne validates, fetches, verifies and renames a single artifact.
func (c *BeaconClient) stageOne(ctx context.Context, a Artifact, set *stagedSet) *artifactFailure {
	if code, detail := validateArtifactSpec(a); code != "" {
		return &artifactFailure{Artifact: a, Code: code, Detail: detail}
	}
	dest, err := destPolicyFor(hostGOOS(), a.Dest, c.getenv)
	if err != nil {
		return &artifactFailure{Artifact: a, Code: codeDestRefused, Detail: err.Error()}
	}

	// Lstat, not Stat: a symlink already sitting at dest must be refused, not
	// followed. Refusing an existing path also means the beacon never silently
	// overwrites something it did not create — and a leftover from a SIGKILLed
	// run surfaces loudly, with a fix an operator can act on, rather than being
	// clobbered by bytes whose provenance nobody then checks.
	if info, lerr := os.Lstat(dest); lerr == nil {
		kind := "file"
		if info.Mode()&os.ModeSymlink != 0 {
			kind = "symlink"
		} else if info.IsDir() {
			kind = "directory"
		}
		return &artifactFailure{Artifact: a, Code: codeDestRefused,
			Detail: fmt.Sprintf("destination %s already exists (%s) — refusing to overwrite a path this beacon did not create; remove it and re-run", dest, kind)}
	}

	created, err := mkdirAllRecording(filepath.Dir(dest))
	set.dirs = append(set.dirs, created...)
	if err != nil {
		return &artifactFailure{Artifact: a, Code: codeWriteFailed,
			Detail: fmt.Sprintf("could not create staging directory %s: %v", filepath.Dir(dest), err)}
	}

	part := dest + stalePartSuffix
	// A leftover .part from a SIGKILLed run would make O_EXCL fail forever.
	_ = os.Remove(part)

	// 1. Check local SHA-256 cache first
	cacheDir := c.getArtifactCacheDir()
	cachedPath := filepath.Join(cacheDir, strings.ToLower(a.SHA256))
	var fromCache bool
	var wrote int64
	var digest string

	if !c.DisableArtifactCache && cacheDir != "" {
		if valid, size := verifyFileSHA256(cachedPath, a.SHA256); valid {
			if _, err := copyFile(cachedPath, part, openFlags(), 0o644); err == nil {
				fromCache = true
				wrote = size
				digest = strings.ToLower(a.SHA256)
			} else {
				_ = os.Remove(part)
			}
		}
	}

	if fromCache {
		if err := applyMode(part, a.Mode); err != nil {
			_ = os.Remove(part)
			return &artifactFailure{Artifact: a, Code: codeWriteFailed,
				Detail: fmt.Sprintf("chmod %s %s: %v", modeOrDefault(a.Mode), part, err)}
		}
		if err := os.Rename(part, dest); err != nil {
			_ = os.Remove(part)
			return &artifactFailure{Artifact: a, Code: codeWriteFailed,
				Detail: fmt.Sprintf("rename %s -> %s: %v", part, dest, err)}
		}
		set.Files = append(set.Files, stagedFile{Artifact: a, Dest: dest, SHA256: digest, Size: wrote, FromCache: true})
		log.Printf("[beacon] staged %s -> %s (cache-hit) sha256=%s", a.Name, dest, shortDigest(digest))
		return nil
	}

	var last *artifactFailure
	for attempt := 1; attempt <= artifactRetries; attempt++ {
		fail, retryable, w, d := c.fetchArtifactOnce(ctx, a, part)
		if fail == nil {
			wrote = w
			digest = d

			// Populate local SHA-256 cache for future runs
			if !c.DisableArtifactCache && cacheDir != "" {
				_ = os.MkdirAll(cacheDir, 0o755)
				cachePart := cachedPath + stalePartSuffix
				_ = os.Remove(cachePart)
				if _, cerr := copyFile(part, cachePart, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644); cerr == nil {
					_ = os.Rename(cachePart, cachedPath)
				} else {
					_ = os.Remove(cachePart)
				}
			}

			// ---- ORDERING IS THE GUARANTEE ----
			// The step's command names `dest`. `dest` does not exist until the
			// digest matched. It is structurally impossible for a step to find a
			// runnable-but-unverified file. There is no CHECKSUM_SKIPPED path
			// and there must never be one.
			if err := applyMode(part, a.Mode); err != nil {
				_ = os.Remove(part)
				return &artifactFailure{Artifact: a, Code: codeWriteFailed,
					Detail: fmt.Sprintf("chmod %s %s: %v", modeOrDefault(a.Mode), part, err)}
			}
			if err := os.Rename(part, dest); err != nil {
				_ = os.Remove(part)
				return &artifactFailure{Artifact: a, Code: codeWriteFailed,
					Detail: fmt.Sprintf("rename %s -> %s: %v", part, dest, err)}
			}
			set.Files = append(set.Files, stagedFile{Artifact: a, Dest: dest, SHA256: digest, Size: wrote})
			log.Printf("[beacon] staged %s -> %s sha256=%s", a.Name, dest, shortDigest(digest))
			return nil
		}
		_ = os.Remove(part)
		last = fail
		if !retryable || attempt == artifactRetries {
			break
		}
		// Retry ONLY transport errors and 5xx. Never a 404/403 (a missing
		// artifact stays missing; retrying delays the diagnosis) and NEVER a
		// digest mismatch — retrying a mismatch is how a security signal becomes
		// a flake that gets triaged as network noise.
		backoff := time.Duration(attempt) * artifactRetryBase
		log.Printf("[beacon] artifact %s attempt %d/%d failed (%s) — retrying in %s",
			a.Name, attempt, artifactRetries, fail.Code, backoff)
		select {
		case <-ctx.Done():
			return last
		case <-time.After(backoff):
		}
	}
	return last
}

// fetchArtifactOnce performs one GET, streaming into part while hashing.
// Returns (failure, retryable, bytesWritten, digest).
func (c *BeaconClient) fetchArtifactOnce(ctx context.Context, a Artifact, part string) (*artifactFailure, bool, int64, string) {
	fetchCtx, cancel := context.WithTimeout(ctx, artifactFetchTimeout)
	defer cancel()

	url := c.ServerURL + a.Path
	req, err := http.NewRequestWithContext(fetchCtx, http.MethodGet, url, nil)
	if err != nil {
		return &artifactFailure{Artifact: a, Code: codeSpecInvalid,
			Detail: fmt.Sprintf("cannot build request for %s: %v", url, err)}, false, 0, ""
	}
	req.Header.Set("Accept", "application/octet-stream")
	if c.artifactToken != "" {
		req.Header.Set("Authorization", "Bearer "+c.artifactToken)
	}

	resp, err := c.artifactClient().Do(req)
	if err != nil {
		return &artifactFailure{Artifact: a, Code: codeUnreachable,
			Detail: fmt.Sprintf("GET %s: %v", url, err)}, true, 0, ""
	}
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		_ = resp.Body.Close()
	}()

	if fail, retryable := classifyStatus(a, url, resp); fail != nil {
		return fail, retryable, 0, ""
	}

	f, err := os.OpenFile(part, openFlags(), 0o644)
	if err != nil {
		// O_EXCL refuses an existing name; O_NOFOLLOW (POSIX) refuses a
		// final-component symlink. Both are syscall-level, so there is no
		// check-then-create race to lose.
		return &artifactFailure{Artifact: a, Code: codeWriteFailed,
			Detail: fmt.Sprintf("open %s: %v", part, err)}, false, 0, ""
	}

	h := sha256.New()
	wrote, copyErr := io.Copy(io.MultiWriter(f, h), io.LimitReader(resp.Body, maxArtifactBytes+1))
	closeErr := f.Close()
	if copyErr != nil {
		return &artifactFailure{Artifact: a, Code: codeTruncated,
			Detail: fmt.Sprintf("body read failed after %d bytes from %s: %v", wrote, url, copyErr)}, true, wrote, ""
	}
	if closeErr != nil {
		return &artifactFailure{Artifact: a, Code: codeWriteFailed,
			Detail: fmt.Sprintf("close %s: %v", part, closeErr)}, false, wrote, ""
	}
	if wrote > maxArtifactBytes {
		return &artifactFailure{Artifact: a, Code: codeTooLarge,
			Detail: fmt.Sprintf("body exceeds the %d-byte cap (source=%s)", maxArtifactBytes, url)}, false, wrote, ""
	}
	if a.SizeBytes > 0 && wrote != a.SizeBytes {
		return &artifactFailure{Artifact: a, Code: codeTruncated,
			Detail: fmt.Sprintf("expected %d bytes, received %d (source=%s) — look for a truncating proxy",
				a.SizeBytes, wrote, url)}, true, wrote, ""
	}

	digest := hex.EncodeToString(h.Sum(nil))
	// Plain lowercase-hex compare. crypto/subtle is deliberately NOT used: there
	// is no secret here, constant-time buys nothing and implies a threat model
	// that does not apply.
	if digest != strings.ToLower(a.SHA256) {
		return &artifactFailure{Artifact: a, Code: codeSHA256Mismatch,
			Detail: fmt.Sprintf("want=%s\n   got =%s\n   the bytes changed between the DC's SimCore and this host; look for an intercepting proxy",
				strings.ToLower(a.SHA256), digest)}, false, wrote, digest
	}
	return nil, false, wrote, digest
}

// getArtifactCacheDir resolves the local content-addressable cache directory.
func (c *BeaconClient) getArtifactCacheDir() string {
	if c.artifactCacheDirFunc != nil {
		return c.artifactCacheDirFunc()
	}
	if env := c.getenv("CORTEXSIM_CACHE_DIR"); env != "" {
		_ = os.MkdirAll(env, 0o755)
		return env
	}
	// Try /opt/cortexsim/cache if writable
	optCache := "/opt/cortexsim/cache"
	if err := os.MkdirAll(optCache, 0o755); err == nil {
		testFile := filepath.Join(optCache, ".cxs_test")
		if err := os.WriteFile(testFile, []byte("ok"), 0o644); err == nil {
			_ = os.Remove(testFile)
			return optCache
		}
	}
	// Try user cache dir
	if ucd, err := os.UserCacheDir(); err == nil && ucd != "" {
		userCache := filepath.Join(ucd, "cortexsim")
		if err := os.MkdirAll(userCache, 0o755); err == nil {
			return userCache
		}
	}
	// Fallback to os.TempDir()/cortexsim-cache
	fallback := filepath.Join(os.TempDir(), "cortexsim-cache")
	_ = os.MkdirAll(fallback, 0o755)
	return fallback
}

// copyFile copies bytes from src to dst with specific flags and file mode.
func copyFile(src, dst string, flags int, mode os.FileMode) (int64, error) {
	in, err := os.Open(src)
	if err != nil {
		return 0, err
	}
	defer in.Close()

	out, err := os.OpenFile(dst, flags, mode)
	if err != nil {
		return 0, err
	}
	defer out.Close()

	n, err := io.Copy(out, in)
	if err != nil {
		return n, err
	}
	return n, out.Close()
}

// verifyFileSHA256 reads path and reports whether its sha256 digest matches expected.
func verifyFileSHA256(path, expectedSHA256 string) (bool, int64) {
	f, err := os.Open(path)
	if err != nil {
		return false, 0
	}
	defer f.Close()

	h := sha256.New()
	n, err := io.Copy(h, f)
	if err != nil {
		return false, 0
	}
	digest := hex.EncodeToString(h.Sum(nil))
	return strings.EqualFold(digest, expectedSHA256), n
}

// findExecutableFallback identifies an alternative staging directory when a noexec mount is detected.
func (c *BeaconClient) findExecutableFallback(currentDir string) string {
	candidates := []string{
		"/opt/cortexsim/tools",
		"/var/tmp",
		"/dev/shm",
	}
	if home := c.getenv("HOME"); home != "" {
		candidates = append(candidates, filepath.Join(home, ".cortexsim", "tools"))
	}
	for _, cand := range candidates {
		if cand == currentDir {
			continue
		}
		if err := os.MkdirAll(cand, 0o755); err != nil {
			continue
		}
		probe := filepath.Join(cand, ".cxs_noexec_probe")
		if err := os.WriteFile(probe, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
			continue
		}
		_ = os.Chmod(probe, 0o755)
		execErr := c.probeExec(probe)
		_ = os.Remove(probe)
		if execErr == nil {
			return cand
		}
	}
	return ""
}

// classifyStatus maps an HTTP response onto the taxonomy. Returns
// (failure, retryable); a nil failure means 2xx.
func classifyStatus(a Artifact, url string, resp *http.Response) (*artifactFailure, bool) {
	switch {
	case resp.StatusCode >= 200 && resp.StatusCode < 300:
		return nil, false
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusForbidden:
		return &artifactFailure{Artifact: a, Code: codeForbidden,
			Detail: fmt.Sprintf("HTTP %d from %s — the shelf is in token mode; supply --artifact-token / CORTEXSIM_ARTIFACT_TOKEN",
				resp.StatusCode, url)}, false
	case resp.StatusCode >= 300 && resp.StatusCode < 400:
		// CheckRedirect refuses to follow: the fetch origin is fixed by the
		// operator at install time, and following a redirect would carry the
		// bearer token to a host the operator never named.
		return &artifactFailure{Artifact: a, Code: codeUnavailable,
			Detail: fmt.Sprintf("HTTP %d from %s (Location: %s) — redirects are NOT followed; the fetch origin is fixed at install time",
				resp.StatusCode, url, resp.Header.Get("Location"))}, false
	case resp.StatusCode >= 500:
		return &artifactFailure{Artifact: a, Code: codeUnreachable,
			Detail: fmt.Sprintf("HTTP %d from %s", resp.StatusCode, url)}, true
	default:
		return &artifactFailure{Artifact: a, Code: codeUnavailable,
			Detail: fmt.Sprintf("HTTP %d from %s — run ./scripts/build-payloads.sh on the SimCore host, or rebuild the image",
				resp.StatusCode, url)}, false
	}
}

// -------------------------------------------------------------------------
// Spec + destination validation
// -------------------------------------------------------------------------

var (
	hexDigestRE    = regexp.MustCompile(`^[0-9a-f]{64}$`)
	artifactNameRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`)
	modeRE         = regexp.MustCompile(`^0?[0-7]{3}$`)
	uuidRE         = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
)

// validateArtifactSpec rejects a plan that cannot be honoured. Returns
// ("", "") when the spec is well-formed.
func validateArtifactSpec(a Artifact) (string, string) {
	if !artifactNameRE.MatchString(a.Name) {
		return codeSpecInvalid, fmt.Sprintf("artifact name %q is not a bare shelf filename", a.Name)
	}
	if !hexDigestRE.MatchString(strings.ToLower(a.SHA256)) {
		// A blank or short digest is the one thing that must never degrade to a
		// skipped check: comparing against "" passes on anything.
		return codeSpecInvalid, fmt.Sprintf(
			"artifact %s carries sha256=%q — a digest must be 64 lowercase hex; an absent digest is a REFUSAL, never a skipped check",
			a.Name, a.SHA256)
	}
	if a.Path == "" || !strings.HasPrefix(a.Path, "/") || strings.Contains(a.Path, "://") {
		return codeSpecInvalid, fmt.Sprintf(
			"artifact %s path=%q must be SERVER-RELATIVE (an absolute URL in a task would let task data redirect the fetch off the operator-fixed origin)",
			a.Name, a.Path)
	}
	if a.Dest == "" {
		return codeSpecInvalid, fmt.Sprintf("artifact %s has no dest", a.Name)
	}
	if a.Mode != "" && !modeRE.MatchString(a.Mode) {
		return codeSpecInvalid, fmt.Sprintf("artifact %s mode=%q is not 3-digit POSIX octal", a.Name, a.Mode)
	}
	return "", ""
}

func modeOrDefault(mode string) string {
	if mode == "" {
		return defaultArtifactMode
	}
	return mode
}

func parseMode(mode string) (os.FileMode, error) {
	v, err := strconv.ParseUint(modeOrDefault(mode), 8, 32)
	if err != nil {
		return 0, err
	}
	return os.FileMode(v), nil
}

// destPolicyFor validates a server-supplied destination against a compiled-in,
// per-platform allowlist.
//
// The beacon frequently runs as root. An unchecked dest would turn the task
// queue into a remote arbitrary-file-write primitive on a customer endpoint —
// a far worse outcome than anything the simulation itself does.
//
// goos and getenv are parameters, not globals, so the Windows policy is
// exercised from a Linux runner (the established identity.ResolveFor pattern).
func destPolicyFor(goos, dest string, getenv func(string) string) (string, error) {
	if getenv == nil {
		getenv = os.Getenv
	}
	if dest == "" {
		return "", errors.New("empty destination")
	}
	if goos == "windows" {
		return windowsDestPolicy(dest, getenv)
	}
	return posixDestPolicy(dest, getenv)
}

func posixDestPolicy(dest string, getenv func(string) string) (string, error) {
	if strings.Contains(dest, `\`) || winDriveRE.MatchString(dest) {
		return "", fmt.Errorf("destination %q is a Windows path on a POSIX host — the server used the wrong Agent.os", dest)
	}
	if !strings.HasPrefix(dest, "/") {
		return "", fmt.Errorf("destination %q is not absolute", dest)
	}
	if hasDotDot(strings.Split(dest, "/")) {
		return "", fmt.Errorf("destination %q contains a '..' path element", dest)
	}
	clean := path.Clean(dest)
	for _, root := range posixStageAllowlist(getenv) {
		if underPosix(clean, root) {
			return clean, nil
		}
	}
	return "", fmt.Errorf("destination %q is outside the staging allowlist %v (set CORTEXSIM_STAGE_ROOT to widen it)",
		clean, posixStageAllowlist(getenv))
}

var winDriveRE = regexp.MustCompile(`^[A-Za-z]:[\\/]`)

func windowsDestPolicy(dest string, getenv func(string) string) (string, error) {
	if strings.HasPrefix(dest, `\\`) || strings.HasPrefix(dest, "//") {
		// A UNC destination is a write to a REMOTE share.
		return "", fmt.Errorf("destination %q is a UNC path — refused", dest)
	}
	if strings.HasPrefix(dest, "/") {
		return "", fmt.Errorf("destination %q is a POSIX path on a Windows host — the server used the wrong Agent.os", dest)
	}
	if !winDriveRE.MatchString(dest) {
		return "", fmt.Errorf("destination %q is not drive-letter absolute", dest)
	}
	norm := strings.ReplaceAll(dest, "/", `\`)
	parts := strings.Split(norm, `\`)
	if hasDotDot(parts) {
		return "", fmt.Errorf("destination %q contains a '..' path element", dest)
	}
	clean := cleanWindows(norm)
	for _, root := range windowsStageAllowlist(getenv) {
		if underWindows(clean, root) {
			return clean, nil
		}
	}
	return "", fmt.Errorf("destination %q is outside the staging allowlist %v (set CORTEXSIM_STAGE_ROOT to widen it)",
		clean, windowsStageAllowlist(getenv))
}

func hasDotDot(parts []string) bool {
	for _, p := range parts {
		if p == ".." {
			return true
		}
	}
	return false
}

func cleanWindows(p string) string {
	parts := strings.Split(p, `\`)
	out := parts[:0]
	for i, s := range parts {
		if s == "" && i > 0 {
			continue
		}
		if s == "." {
			continue
		}
		out = append(out, s)
	}
	return strings.Join(out, `\`)
}

func posixStageAllowlist(getenv func(string) string) []string {
	roots := []string{"/tmp", "/var/tmp", "/dev/shm", "/opt/cortexsim"}
	if r := strings.TrimSpace(getenv("CORTEXSIM_STAGE_ROOT")); r != "" && strings.HasPrefix(r, "/") {
		roots = append(roots, path.Clean(r))
	}
	return roots
}

func windowsStageAllowlist(getenv func(string) string) []string {
	var roots []string
	add := func(v string) {
		v = strings.TrimSpace(v)
		if v != "" && winDriveRE.MatchString(v) {
			roots = append(roots, cleanWindows(strings.TrimSuffix(strings.ReplaceAll(v, "/", `\`), `\`)))
		}
	}
	add(getenv("TEMP"))
	windir := strings.TrimSpace(getenv("windir"))
	if windir == "" {
		windir = `C:\Windows`
	}
	add(windir + `\Temp`)
	pd := strings.TrimSpace(getenv("ProgramData"))
	if pd == "" {
		pd = `C:\ProgramData`
	}
	add(pd + `\CortexSim`)
	add(getenv("CORTEXSIM_STAGE_ROOT"))
	return roots
}

func underPosix(p, root string) bool {
	root = strings.TrimSuffix(root, "/")
	return p == root || strings.HasPrefix(p, root+"/")
}

func underWindows(p, root string) bool {
	lp, lr := strings.ToLower(p), strings.ToLower(strings.TrimSuffix(root, `\`))
	return lp == lr || strings.HasPrefix(lp, lr+`\`)
}

// stageRootFor is the default staging tree this beacon owns. It is used for the
// allowlist and for the stale-staging sweep; the beacon NEVER invents a dest
// (the server computes it, §1.4) — inventing one is how a Windows beacon ends up
// writing to /tmp.
func stageRootFor(goos string, getenv func(string) string) string {
	if getenv == nil {
		getenv = os.Getenv
	}
	if r := strings.TrimSpace(getenv("CORTEXSIM_STAGE_ROOT")); r != "" {
		return r
	}
	if goos == "windows" {
		windir := strings.TrimSpace(getenv("windir"))
		if windir == "" {
			windir = `C:\Windows`
		}
		return windir + `\Temp\cortexsim`
	}
	return "/tmp/.cortexsim"
}

// -------------------------------------------------------------------------
// Directory creation (recorded, so Cleanup removes ONLY what we made)
// -------------------------------------------------------------------------

func mkdirAllRecording(dir string) ([]string, error) {
	if dir == "" {
		return nil, errors.New("empty directory")
	}
	var missing []string
	cur := dir
	for {
		if _, err := os.Stat(cur); err == nil {
			break
		}
		missing = append(missing, cur)
		parent := filepath.Dir(cur)
		if parent == cur {
			break
		}
		cur = parent
	}
	// Outermost first.
	for i, j := 0, len(missing)-1; i < j; i, j = i+1, j-1 {
		missing[i], missing[j] = missing[j], missing[i]
	}
	for _, d := range missing {
		// 0755, NOT 0700. The fetch runs as the beacon's account (commonly root)
		// and the step runs as www-data via `runuser -l`. A root-owned 0700
		// staging dir makes the step die EACCES — a "Permission denied" that
		// reads exactly like the TTP failing on a hardened host. This is the
		// single most likely field break in the whole feature.
		if err := os.Mkdir(d, 0o755); err != nil && !os.IsExist(err) {
			return missing, err
		}
		// Mkdir honours umask; force the mode so a umask 077 beacon does not
		// silently recreate the 0700 trap.
		_ = os.Chmod(d, 0o755)
	}
	return missing, nil
}

// -------------------------------------------------------------------------
// noexec pre-flight
// -------------------------------------------------------------------------

// probeExecutability converts a `noexec` mount — which fails at EXEC time,
// mid-scenario, looking like the TTP failing on a hardened host — into a named
// pre-flight failure.
func (c *BeaconClient) probeExecutability(set *stagedSet) *artifactFailure {
	if !execProbeSupported() {
		// Windows has no noexec mount concept. AppLocker/WDAC CAN block
		// execution and cannot be pre-flighted — stated, not papered over.
		return nil
	}
	seen := map[string]bool{}
	for _, f := range set.Files {
		m, err := parseMode(f.Artifact.Mode)
		if err != nil || m&0o111 == 0 {
			continue
		}
		dir := filepath.Dir(f.Dest)
		if seen[dir] {
			continue
		}
		seen[dir] = true
		probe := filepath.Join(dir, execProbeName)
		if err := os.WriteFile(probe, []byte("#!/bin/sh\nexit 0\n"), 0o755); err != nil {
			// Cannot probe — not a reason to fail the run; the write failure
			// would already have surfaced on the artifact itself.
			continue
		}
		_ = os.Chmod(probe, 0o755)
		set.probes = append(set.probes, probe)
		if err := c.probeExec(probe); err != nil {
			fallback := c.findExecutableFallback(dir)
			if fallback != "" {
				return &artifactFailure{Artifact: f.Artifact, Code: codeDestNoexec,
					Detail: fmt.Sprintf("%s cannot execute files (%v) — noexec mount detected; fallback candidate %s is available (set CORTEXSIM_STAGE_ROOT=%s)", dir, err, fallback, fallback)}
			}
			return &artifactFailure{Artifact: f.Artifact, Code: codeDestNoexec,
				Detail: fmt.Sprintf("%s cannot execute files (%v) — likely a noexec mount; set CORTEXSIM_STAGE_ROOT=/var/tmp on the beacon", dir, err)}
		}
	}
	return nil
}

// -------------------------------------------------------------------------
// Teardown
// -------------------------------------------------------------------------

// Cleanup removes exactly the files and directories this staging phase created.
//
// Pull mode NEVER executes a scenario's cleanup.commands (only push_generator
// emits those), so the beacon owns removal outright — it cannot be delegated to
// content. Staged offensive tooling that survives a run on a customer endpoint
// is found either at engagement close-out or by the customer's own EDR.
//
// Directories are removed with os.Remove (empty-only), innermost-first — never
// RemoveAll — so a path we did not write is never touched.
//
// Cleanup errors NEVER change the run's exit code (a failed rm must not
// manufacture a FAILED run out of a successful simulation) but are always
// reported: a POV that leaves offensive tooling on a customer endpoint and does
// not say so is strictly worse than one that leaves it and does.
func (s *stagedSet) Cleanup(c *BeaconClient, runID string) {
	if s == nil {
		return
	}
	var residue []string

	for _, p := range s.probes {
		if err := os.Remove(p); err != nil && !os.IsNotExist(err) {
			residue = append(residue, fmt.Sprintf("%s could not be removed: %v", p, err))
		}
	}
	for _, f := range s.Files {
		if err := os.Remove(f.Dest); err != nil && !os.IsNotExist(err) {
			residue = append(residue, fmt.Sprintf("%s could not be removed: %v", f.Dest, err))
		}
	}
	// Innermost-first.
	for i := len(s.dirs) - 1; i >= 0; i-- {
		d := s.dirs[i]
		if err := os.Remove(d); err != nil && !os.IsNotExist(err) {
			// A non-empty directory is NOT residue we planted — something else
			// put a file there. Say nothing rather than cry wolf.
			if entries, rerr := os.ReadDir(d); rerr == nil && len(entries) == 0 {
				residue = append(residue, fmt.Sprintf("%s could not be removed: %v", d, err))
			}
		}
	}

	if len(residue) == 0 || c == nil || runID == "" {
		return
	}
	var b strings.Builder
	for _, r := range residue {
		fmt.Fprintf(&b, "%s %s\n", artifactResidueMarker, r)
	}
	b.WriteString("   Remove it manually before engagement close-out.")
	if err := c.SendOutput(runID, "", b.String()); err != nil {
		log.Printf("[beacon] could not report artifact residue for run %s: %v", runID, err)
	}
	log.Printf("[beacon] ARTIFACT RESIDUE run_id=%s: %s", runID, strings.Join(residue, "; "))
}

// SweepStaleStaging removes run directories the beacon left behind when it was
// SIGKILLed, OOM-killed, or the host rebooted — the cases where a deferred
// Cleanup does not run.
//
// It removes ONLY directories directly under the stage root whose name is a
// run-id UUID. That guard is load-bearing: CORTEXSIM_STAGE_ROOT may legitimately
// be /var/tmp, and a blanket sweep there would delete other people's files.
//
// LIMIT, stated rather than implied: an operator-chosen absolute dest OUTSIDE
// the run tree (the rename negative control's /tmp/.cache/sysinfo.sh) is covered
// by the deferred Cleanup alone. If the process is SIGKILLed, that file survives.
func SweepStaleStaging() int {
	return sweepStaleStagingIn(stageRootFor(hostGOOS(), os.Getenv), time.Now(), staleStagingMaxAge)
}

func sweepStaleStagingIn(root string, now time.Time, maxAge time.Duration) int {
	entries, err := os.ReadDir(root)
	if err != nil {
		return 0
	}
	removed := 0
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Strings(names)
	for _, name := range names {
		if !uuidRE.MatchString(name) {
			continue
		}
		full := filepath.Join(root, name)
		info, err := os.Stat(full)
		if err != nil || !info.IsDir() {
			continue
		}
		if now.Sub(info.ModTime()) < maxAge {
			continue
		}
		if err := os.RemoveAll(full); err == nil {
			removed++
			log.Printf("[beacon] swept stale staging directory %s", full)
		}
	}
	return removed
}

// -------------------------------------------------------------------------
// Failure reporting — the part that must not be cut
// -------------------------------------------------------------------------

// reportStagingFailure writes one /output frame per step that needed a failed
// artifact, then completes the run with exit 78.
//
// Without this the run reads as "the TTP ran and Cortex saw nothing" — a
// manufactured false negative in a customer-facing report.
func (c *BeaconClient) reportStagingFailure(task *Task, set *stagedSet) {
	total := len(task.Steps)
	indexOf := map[string]int{}
	for i := range task.Steps {
		indexOf[task.Steps[i].ID] = i
	}

	// step index -> failures attributed to it. A failure that names no steps is
	// attributed to step 1 (it blocked the run regardless).
	perStep := map[int][]artifactFailure{}
	codes := make([]string, 0, len(set.Failures))
	seenCode := map[string]bool{}
	for _, f := range set.Failures {
		if !seenCode[f.Code] {
			seenCode[f.Code] = true
			codes = append(codes, f.Code)
		}
		targets := f.Artifact.Steps
		if len(targets) == 0 {
			perStep[0] = append(perStep[0], f)
			continue
		}
		for _, sid := range targets {
			idx, ok := indexOf[sid]
			if !ok {
				idx = 0
			}
			perStep[idx] = append(perStep[idx], f)
		}
	}

	order := make([]int, 0, len(perStep))
	for idx := range perStep {
		order = append(order, idx)
	}
	sort.Ints(order)

	for _, idx := range order {
		if idx >= total {
			continue
		}
		step := task.Steps[idx]
		frame := formatStagingFailureFrame(idx+1, total, &step, c.resolveStepUsername(task, &step), perStep[idx], c.ServerURL)
		if err := c.SendOutput(task.RunID, step.ID, frame); err != nil {
			log.Printf("[beacon] sendOutput (staging failure, step %s) error: %v", step.ID, err)
		}
	}

	// One un-attributed frame so the failure is legible even if step ids drift.
	var b strings.Builder
	fmt.Fprintf(&b, "\n=== ARTIFACT STAGING · %d artifact(s) ===\n", set.Total)
	for _, f := range set.Failures {
		fmt.Fprintf(&b, "%s %s\n   code=%s\n   %s\n   source=%s%s\n   dest=%s\n",
			artifactNotStagedMarker, f.Artifact.Name, f.Code, f.Detail,
			c.ServerURL, f.Artifact.Path, f.Artifact.Dest)
	}
	fmt.Fprintf(&b, "%s %d of %d artifact(s) could not be staged — NO STEPS RAN.\n",
		artifactStagingFailedMarker, len(set.Failures), set.Total)
	if err := c.SendOutput(task.RunID, "", b.String()); err != nil {
		log.Printf("[beacon] sendOutput (staging failure summary) error: %v", err)
	}

	summary := fmt.Sprintf(
		"FAILED (exit %d) | scenario=%s steps=%d | %s %d of %d artifact(s) could not be staged (%s) — NO STEPS RAN. This run proves NOTHING about the customer's detection coverage.",
		artifactStagingExitCode, task.ScenarioID, total, artifactStagingFailedMarker,
		len(set.Failures), set.Total, strings.Join(codes, ","))
	if err := c.Complete(task.RunID, artifactStagingExitCode, summary); err != nil {
		log.Printf("[beacon] complete (staging failure) error: %v", err)
	}
	log.Printf("[beacon] %s run_id=%s scenario=%s codes=%s", artifactStagingFailedMarker,
		task.RunID, task.ScenarioID, strings.Join(codes, ","))
}

// formatStagingFailureFrame reuses formatStepOutput's header shape so the run
// timeline reads consistently, and states in plain language that the step did
// not run.
func formatStagingFailureFrame(n, total int, step *Step, username string, fails []artifactFailure, server string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "=== STEP %d/%d · %s · %s · identity=%s ===\n",
		n, total, step.ID, step.MitreTechnique, username)
	for _, f := range fails {
		fmt.Fprintf(&b, "%s %s\n", artifactNotStagedMarker, f.Artifact.Name)
		fmt.Fprintf(&b, "   code=%s\n", f.Code)
		fmt.Fprintf(&b, "   %s\n", f.Detail)
		fmt.Fprintf(&b, "   source=%s%s\n", server, f.Artifact.Path)
		fmt.Fprintf(&b, "   dest=%s\n", f.Artifact.Dest)
	}
	b.WriteString("   THIS STEP DID NOT RUN. The absence of a detection here is a CortexSim\n")
	b.WriteString("   staging failure, NOT a gap in the customer's detection coverage.\n")
	fmt.Fprintf(&b, "--- exit_code=%d duration=0s ---\n", artifactStagingExitCode)
	return b.String()
}
