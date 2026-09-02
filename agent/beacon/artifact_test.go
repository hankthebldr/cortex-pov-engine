package beacon

// Tests for artifact staging.
//
// What a Linux CI runner can prove is asserted here. What it CANNOT prove is
// listed at the bottom of this file rather than silently omitted — an untested
// guarantee that reads as tested is the same class of lie the taxonomy exists
// to prevent.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// -----------------------------------------------------------------------------
// helpers
// -----------------------------------------------------------------------------

func digestOf(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

// shelfServer serves /api/shelf/payload/{name} from an in-memory map and counts
// every request, so "never retry a digest mismatch" is asserted by counting
// rather than by reading the code.
type shelfServer struct {
	*httptest.Server
	hits    atomic.Int64
	body    []byte
	status  int
	headers map[string]string
	stall   time.Duration
}

func newShelfServer(t *testing.T, body []byte) *shelfServer {
	t.Helper()
	s := &shelfServer{body: body, status: 200, headers: map[string]string{}}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.hits.Add(1)
		for k, v := range s.headers {
			w.Header().Set(k, v)
		}
		if s.stall > 0 {
			w.WriteHeader(s.status)
			flusher, _ := w.(http.Flusher)
			_, _ = w.Write([]byte("partial"))
			if flusher != nil {
				flusher.Flush()
			}
			time.Sleep(s.stall)
			return
		}
		if s.status != 200 {
			w.WriteHeader(s.status)
			_, _ = w.Write([]byte("error"))
			return
		}
		_, _ = w.Write(s.body)
	}))
	t.Cleanup(s.Close)
	return s
}

// stagingClient builds a BeaconClient whose dest allowlist is rooted at a temp
// directory, so the tests never touch a real /tmp/.cortexsim.
func stagingClient(t *testing.T, serverURL, stageRoot string) *BeaconClient {
	t.Helper()
	c := New(serverURL, "agent-test", time.Millisecond)
	c.artifactGetenv = func(k string) string {
		if k == "CORTEXSIM_STAGE_ROOT" {
			return stageRoot
		}
		return ""
	}
	cacheDir := t.TempDir()
	c.artifactCacheDirFunc = func() string { return cacheDir }
	return c
}

func shortRetries(t *testing.T) {
	t.Helper()
	old := artifactRetryBase
	artifactRetryBase = time.Millisecond
	t.Cleanup(func() { artifactRetryBase = old })
}

func stageOneArtifact(t *testing.T, c *BeaconClient, a Artifact) *stagedSet {
	t.Helper()
	task := &Task{RunID: "run-1", ScenarioID: "SIM-TEST-001", Artifacts: []Artifact{a}}
	return c.stageArtifacts(context.Background(), task)
}

func onlyFailure(t *testing.T, set *stagedSet) artifactFailure {
	t.Helper()
	if len(set.Failures) != 1 {
		t.Fatalf("want exactly 1 failure, got %d: %+v", len(set.Failures), set.Failures)
	}
	return set.Failures[0]
}

// -----------------------------------------------------------------------------
// 1 · Wire decode — the whole contract, including its absence
// -----------------------------------------------------------------------------

func TestTaskDecode_ArtifactsAbsentIsNilAndChangesNothing(t *testing.T) {
	raw := `{"task_id":"t","run_id":"r","scenario_id":"S","steps":[],"identity_context":null,` +
		`"identity_default":null,"created_at":"2026-08-05T12:00:00.000000"}`
	var task Task
	if err := json.Unmarshal([]byte(raw), &task); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if task.Artifacts != nil {
		t.Fatalf("a task with no artifacts key must decode to a nil slice, got %+v", task.Artifacts)
	}
	if len(task.Artifacts) != 0 {
		t.Fatalf("len must be 0 so the staging phase is a no-op")
	}
}

func TestTaskDecode_EmptyArtifactListIsAlsoANoOp(t *testing.T) {
	var task Task
	if err := json.Unmarshal([]byte(`{"task_id":"t","run_id":"r","scenario_id":"S","artifacts":[]}`), &task); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(task.Artifacts) != 0 {
		t.Fatalf("empty artifacts must stay empty")
	}
}

func TestTaskDecode_FullArtifactContract(t *testing.T) {
	raw := `{"task_id":"t","run_id":"r","scenario_id":"SIM-CDR-001","steps":[],
	  "artifacts":[{"name":"linpeas.sh","sha256":"0ea7e9ce5fcca464b5998cb73930e36647ebf9b38590fe250bbd5664fe8670a5",
	    "size_bytes":1106683,"path":"/api/shelf/payload/linpeas.sh","dest":"/tmp/.cache/sysinfo.sh",
	    "mode":"0755","steps":["step-05"]}]}`
	var task Task
	if err := json.Unmarshal([]byte(raw), &task); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(task.Artifacts) != 1 {
		t.Fatalf("want 1 artifact, got %d", len(task.Artifacts))
	}
	a := task.Artifacts[0]
	if a.Name != "linpeas.sh" || a.SizeBytes != 1106683 || a.Dest != "/tmp/.cache/sysinfo.sh" ||
		a.Mode != "0755" || len(a.Steps) != 1 || a.Steps[0] != "step-05" ||
		a.Path != "/api/shelf/payload/linpeas.sh" {
		t.Fatalf("artifact decoded wrong: %+v", a)
	}
}

func TestTaskDecode_MalformedArtifactsIsAnError(t *testing.T) {
	var task Task
	if err := json.Unmarshal([]byte(`{"task_id":"t","artifacts":"linpeas.sh"}`), &task); err == nil {
		t.Fatalf("a string in place of the artifacts array must not decode silently")
	}
}

// -----------------------------------------------------------------------------
// 2 · Spec validation — a blank digest is a refusal, never a skipped check
// -----------------------------------------------------------------------------

func TestValidateArtifactSpec(t *testing.T) {
	good := Artifact{
		Name: "linpeas.sh", SHA256: strings.Repeat("a", 64),
		Path: "/api/shelf/payload/linpeas.sh", Dest: "/tmp/x/linpeas.sh", Mode: "0755",
	}
	if code, detail := validateArtifactSpec(good); code != "" {
		t.Fatalf("well-formed spec rejected: %s %s", code, detail)
	}

	cases := []struct {
		name  string
		mut   func(a *Artifact)
		want  string
		inMsg string
	}{
		{"blank digest", func(a *Artifact) { a.SHA256 = "" }, codeSpecInvalid, "REFUSAL"},
		{"short digest", func(a *Artifact) { a.SHA256 = "abc123" }, codeSpecInvalid, "64 lowercase hex"},
		{"non-hex digest", func(a *Artifact) { a.SHA256 = strings.Repeat("z", 64) }, codeSpecInvalid, "64 lowercase hex"},
		{"empty name", func(a *Artifact) { a.Name = "" }, codeSpecInvalid, "bare shelf filename"},
		{"path traversal in name", func(a *Artifact) { a.Name = "../etc/passwd" }, codeSpecInvalid, "bare shelf filename"},
		{"absolute url path", func(a *Artifact) { a.Path = "https://evil.example/x.sh" }, codeSpecInvalid, "SERVER-RELATIVE"},
		{"relative path", func(a *Artifact) { a.Path = "api/shelf/payload/x" }, codeSpecInvalid, "SERVER-RELATIVE"},
		{"empty dest", func(a *Artifact) { a.Dest = "" }, codeSpecInvalid, "no dest"},
		{"bad mode", func(a *Artifact) { a.Mode = "rwxr-xr-x" }, codeSpecInvalid, "octal"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			a := good
			tc.mut(&a)
			code, detail := validateArtifactSpec(a)
			if code != tc.want {
				t.Fatalf("code = %q, want %q (detail %q)", code, tc.want, detail)
			}
			if !strings.Contains(detail, tc.inMsg) {
				t.Errorf("detail %q does not mention %q", detail, tc.inMsg)
			}
		})
	}
}

// An UPPERCASE digest from a sloppy producer must still verify, not be rejected
// and not silently pass.
func TestValidateArtifactSpec_UppercaseDigestAccepted(t *testing.T) {
	a := Artifact{Name: "x.sh", SHA256: strings.ToUpper(strings.Repeat("a", 64)),
		Path: "/api/shelf/payload/x.sh", Dest: "/tmp/x.sh"}
	if code, _ := validateArtifactSpec(a); code != "" {
		t.Fatalf("uppercase digest rejected: %s", code)
	}
}

// -----------------------------------------------------------------------------
// 3 · Destination policy — the arbitrary-file-write guard
// -----------------------------------------------------------------------------

func TestDestPolicyPOSIX(t *testing.T) {
	env := func(k string) string { return "" }
	ok := []string{"/tmp/.cortexsim/r/linpeas.sh", "/var/tmp/x", "/dev/shm/y", "/opt/cortexsim/z"}
	for _, d := range ok {
		if _, err := destPolicyFor("linux", d, env); err != nil {
			t.Errorf("destPolicyFor(linux, %q) refused: %v", d, err)
		}
	}
	bad := map[string]string{
		"/usr/bin/ls":        "allowlist",
		"/etc/cron.d/x":      "allowlist",
		"tmp/relative":       "absolute",
		"/tmp/../etc/passwd": "'..'",
		`C:\Windows\Temp\x`:  "Windows path on a POSIX host",
	}
	for d, want := range bad {
		_, err := destPolicyFor("linux", d, env)
		if err == nil {
			t.Errorf("destPolicyFor(linux, %q) accepted — this is a remote arbitrary-file-write primitive", d)
			continue
		}
		if !strings.Contains(err.Error(), want) {
			t.Errorf("destPolicyFor(linux, %q) error %q does not mention %q", d, err, want)
		}
	}
}

func TestDestPolicyPOSIX_StageRootWidensAllowlist(t *testing.T) {
	env := func(k string) string {
		if k == "CORTEXSIM_STAGE_ROOT" {
			return "/srv/cortexsim-stage"
		}
		return ""
	}
	if _, err := destPolicyFor("linux", "/srv/cortexsim-stage/r/x.sh", env); err != nil {
		t.Fatalf("CORTEXSIM_STAGE_ROOT did not widen the allowlist: %v", err)
	}
	if _, err := destPolicyFor("linux", "/srv/other/x.sh", env); err == nil {
		t.Fatalf("CORTEXSIM_STAGE_ROOT must widen the allowlist, not disable it")
	}
}

// The Windows policy is exercised from a Linux runner via the explicit goos
// parameter — the established identity.ResolveFor pattern. This is how this repo
// tests platforms it cannot run.
func TestDestPolicyWindows(t *testing.T) {
	env := func(k string) string {
		switch k {
		case "TEMP":
			return `C:\Users\dc\AppData\Local\Temp`
		case "windir":
			return `C:\Windows`
		case "ProgramData":
			return `C:\ProgramData`
		}
		return ""
	}
	ok := []string{
		`C:\Windows\Temp\cortexsim\r\linpeas.ps1`,
		`C:\Users\dc\AppData\Local\Temp\x.exe`,
		`C:\ProgramData\CortexSim\y`,
		// case-insensitive, as NTFS is
		`c:\windows\TEMP\cortexsim\r\z`,
	}
	for _, d := range ok {
		if _, err := destPolicyFor("windows", d, env); err != nil {
			t.Errorf("destPolicyFor(windows, %q) refused: %v", d, err)
		}
	}
	bad := map[string]string{
		`\\fileserver\share\x.exe`:    "UNC",
		`/tmp/.cortexsim/r/linpeas`:   "POSIX path on a Windows host",
		`C:\Windows\System32\evil`:    "allowlist",
		`C:\Windows\Temp\..\evil.ps1`: "'..'",
		`Temp\relative.ps1`:           "drive-letter absolute",
	}
	for d, want := range bad {
		_, err := destPolicyFor("windows", d, env)
		if err == nil {
			t.Errorf("destPolicyFor(windows, %q) accepted", d)
			continue
		}
		if !strings.Contains(err.Error(), want) {
			t.Errorf("destPolicyFor(windows, %q) error %q does not mention %q", d, err, want)
		}
	}
}

func TestStageRootFor(t *testing.T) {
	none := func(string) string { return "" }
	if got := stageRootFor("linux", none); got != "/tmp/.cortexsim" {
		t.Errorf("linux stage root = %q", got)
	}
	if got := stageRootFor("darwin", none); got != "/tmp/.cortexsim" {
		t.Errorf("darwin stage root = %q", got)
	}
	if got := stageRootFor("windows", none); got != `C:\Windows\Temp\cortexsim` {
		t.Errorf("windows stage root = %q", got)
	}
	override := func(k string) string {
		if k == "CORTEXSIM_STAGE_ROOT" {
			return "/var/tmp/cs"
		}
		return ""
	}
	if got := stageRootFor("linux", override); got != "/var/tmp/cs" {
		t.Errorf("override ignored: %q", got)
	}
}

// -----------------------------------------------------------------------------
// 4 · Fetch, verify, rename
// -----------------------------------------------------------------------------

func TestStage_HappyPath_VerifiesAndLands(t *testing.T) {
	body := []byte("#!/bin/sh\necho linpeas\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)

	dest := filepath.Join(root, "run-1", "linpeas.sh")
	set := stageOneArtifact(t, c, Artifact{
		Name: "linpeas.sh", SHA256: digestOf(body), SizeBytes: int64(len(body)),
		Path: "/api/shelf/payload/linpeas.sh", Dest: dest, Mode: "0755", Steps: []string{"step-05"},
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	got, err := os.ReadFile(dest)
	if err != nil {
		t.Fatalf("staged file missing: %v", err)
	}
	if string(got) != string(body) {
		t.Fatalf("staged bytes differ")
	}
	info, _ := os.Stat(dest)
	if info.Mode().Perm() != 0o755 {
		t.Errorf("staged mode = %v, want 0755 (the step runs as another user via runuser)", info.Mode().Perm())
	}
	// The staging directory must be 0755, NOT 0700: a root-owned 0700 dir makes
	// a `runuser -l www-data` step die EACCES, which reads exactly like the TTP
	// failing on a hardened host. This is the single most likely field break.
	dinfo, _ := os.Stat(filepath.Dir(dest))
	if dinfo.Mode().Perm() != 0o755 {
		t.Errorf("staging dir mode = %v, want 0755", dinfo.Mode().Perm())
	}
	if _, err := os.Stat(dest + stalePartSuffix); !os.IsNotExist(err) {
		t.Errorf(".part file survived the rename")
	}
	if !strings.Contains(set.Ledger(), "staged linpeas.sh") {
		t.Errorf("ledger does not name the artifact:\n%s", set.Ledger())
	}
	if !strings.Contains(set.Ledger(), "digests carried in the task") {
		t.Errorf("ledger must state where the digest came from:\n%s", set.Ledger())
	}
	// /output frames are concatenated verbatim into run.output; a frame without
	// a trailing newline runs into the next step's header and the run record
	// becomes unreadable exactly when someone is trying to diagnose it.
	if !strings.HasSuffix(set.Ledger(), "\n") {
		t.Errorf("ledger frame must end with a newline")
	}
}

// THE load-bearing ordering test. dest must not exist unless the digest matched.
func TestStage_DigestMismatch_LeavesNoRunnableFileAndDoesNotRetry(t *testing.T) {
	shortRetries(t)
	srv := newShelfServer(t, []byte("TAMPERED"))
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)

	dest := filepath.Join(root, "run-1", "linpeas.sh")
	set := stageOneArtifact(t, c, Artifact{
		Name: "linpeas.sh", SHA256: digestOf([]byte("ORIGINAL")),
		Path: "/api/shelf/payload/linpeas.sh", Dest: dest, Mode: "0755",
	})
	f := onlyFailure(t, set)
	if f.Code != codeSHA256Mismatch {
		t.Fatalf("code = %s, want %s", f.Code, codeSHA256Mismatch)
	}
	if !strings.Contains(f.Detail, "want=") || !strings.Contains(f.Detail, "got =") {
		t.Errorf("mismatch detail must carry want/got: %q", f.Detail)
	}
	if !strings.Contains(f.Detail, "intercepting proxy") {
		t.Errorf("mismatch detail must point at the real diagnosis: %q", f.Detail)
	}
	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("a file whose digest did not match is on disk at %s — a step could execute it", dest)
	}
	if _, err := os.Stat(dest + stalePartSuffix); !os.IsNotExist(err) {
		t.Fatalf(".part survived a digest mismatch")
	}
	// Retrying a mismatch converts a tampering signal into an intermittent flake
	// that gets triaged as network noise.
	if n := srv.hits.Load(); n != 1 {
		t.Fatalf("digest mismatch was retried %d times — it must never be retried", n)
	}
}

func TestStage_HTTPStatusTaxonomy(t *testing.T) {
	shortRetries(t)
	cases := []struct {
		status   int
		wantCode string
		wantHits int64
	}{
		{404, codeUnavailable, 1},
		{400, codeUnavailable, 1},
		{401, codeForbidden, 1},
		{403, codeForbidden, 1},
		{500, codeUnreachable, artifactRetries},
		{503, codeUnreachable, artifactRetries},
	}
	for _, tc := range cases {
		t.Run(fmt.Sprintf("http_%d", tc.status), func(t *testing.T) {
			srv := newShelfServer(t, []byte("x"))
			srv.status = tc.status
			root := t.TempDir()
			c := stagingClient(t, srv.URL, root)
			set := stageOneArtifact(t, c, Artifact{
				Name: "x.sh", SHA256: digestOf([]byte("x")),
				Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "x.sh"),
			})
			f := onlyFailure(t, set)
			if f.Code != tc.wantCode {
				t.Fatalf("status %d -> %s, want %s", tc.status, f.Code, tc.wantCode)
			}
			if got := srv.hits.Load(); got != tc.wantHits {
				t.Fatalf("status %d produced %d request(s), want %d", tc.status, got, tc.wantHits)
			}
		})
	}
}

func TestStage_ForbiddenNamesTheFix(t *testing.T) {
	srv := newShelfServer(t, []byte("x"))
	srv.status = 403
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf([]byte("x")),
		Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "x.sh"),
	})
	f := onlyFailure(t, set)
	if !strings.Contains(f.Detail, "--artifact-token") {
		t.Fatalf("a 403 must name the fix, not be a mystery: %q", f.Detail)
	}
}

// A redirect must not be followed: the fetch origin is fixed at install time,
// and following one would carry the bearer token off-origin. This repo has
// already shipped a 302-to-a-captive-portal counted as a delivered record.
func TestStage_RedirectIsRefusedNotFollowed(t *testing.T) {
	var elsewhereHits atomic.Int64
	elsewhere := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		elsewhereHits.Add(1)
		_, _ = w.Write([]byte("captive portal"))
	}))
	defer elsewhere.Close()

	srv := newShelfServer(t, nil)
	srv.status = 302
	srv.headers["Location"] = elsewhere.URL + "/login"

	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf([]byte("x")),
		Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "x.sh"),
	})
	f := onlyFailure(t, set)
	if f.Code != codeUnavailable {
		t.Fatalf("code = %s, want %s", f.Code, codeUnavailable)
	}
	if !strings.Contains(f.Detail, "Location") {
		t.Errorf("a refused redirect must name the Location: %q", f.Detail)
	}
	if n := elsewhereHits.Load(); n != 0 {
		t.Fatalf("the beacon followed a redirect off the operator-fixed origin (%d hits)", n)
	}
}

func TestStage_ShortBodyIsTruncatedNotTampering(t *testing.T) {
	shortRetries(t)
	body := []byte("short")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body), SizeBytes: 9999,
		Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "x.sh"),
	})
	f := onlyFailure(t, set)
	// A truncating proxy and a tampering proxy have DIFFERENT fixers, so they
	// must not collapse into one code.
	if f.Code != codeTruncated {
		t.Fatalf("code = %s, want %s", f.Code, codeTruncated)
	}
	if !strings.Contains(f.Detail, "expected 9999 bytes, received 5") {
		t.Errorf("detail must carry both numbers: %q", f.Detail)
	}
}

func TestStage_OverCapIsRefused(t *testing.T) {
	old := maxArtifactBytes
	maxArtifactBytes = 16
	defer func() { maxArtifactBytes = old }()

	body := []byte(strings.Repeat("A", 64))
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "x.sh"),
	})
	f := onlyFailure(t, set)
	if f.Code != codeTooLarge {
		t.Fatalf("code = %s, want %s", f.Code, codeTooLarge)
	}
	if _, err := os.Stat(filepath.Join(root, "x.sh")); !os.IsNotExist(err) {
		t.Fatalf("an over-cap download landed on disk")
	}
}

func TestStage_HangingBodyHitsThePerArtifactDeadline(t *testing.T) {
	shortRetries(t)
	oldTimeout := artifactFetchTimeout
	artifactFetchTimeout = 80 * time.Millisecond
	defer func() { artifactFetchTimeout = oldTimeout }()

	srv := newShelfServer(t, nil)
	srv.stall = 3 * time.Second

	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)

	done := make(chan *stagedSet, 1)
	go func() {
		done <- stageOneArtifact(t, c, Artifact{
			Name: "x.sh", SHA256: digestOf([]byte("x")),
			Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "x.sh"),
		})
	}()
	select {
	case set := <-done:
		f := onlyFailure(t, set)
		if f.Code != codeTruncated && f.Code != codeUnreachable {
			t.Fatalf("a stalled body must fail as a transport problem, got %s", f.Code)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the per-artifact deadline did not fire — a hung shelf would hang the beacon")
	}
}

func TestStage_ExistingDestinationIsRefused(t *testing.T) {
	body := []byte("payload")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)

	dest := filepath.Join(root, "x.sh")
	if err := os.WriteFile(dest, []byte("SOMEBODY ELSE'S FILE"), 0o644); err != nil {
		t.Fatal(err)
	}
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: dest,
	})
	f := onlyFailure(t, set)
	if f.Code != codeDestRefused {
		t.Fatalf("code = %s, want %s", f.Code, codeDestRefused)
	}
	got, _ := os.ReadFile(dest)
	if string(got) != "SOMEBODY ELSE'S FILE" {
		t.Fatalf("the beacon overwrote a file it did not create")
	}
}

func TestStage_SymlinkAtDestinationIsRefusedNotFollowed(t *testing.T) {
	body := []byte("payload")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)

	victim := filepath.Join(root, "victim")
	if err := os.WriteFile(victim, []byte("DO NOT TOUCH"), 0o644); err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(root, "x.sh")
	if err := os.Symlink(victim, dest); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: dest,
	})
	f := onlyFailure(t, set)
	if f.Code != codeDestRefused || !strings.Contains(f.Detail, "symlink") {
		t.Fatalf("a symlink at dest must be refused by name, got %s %q", f.Code, f.Detail)
	}
	got, _ := os.ReadFile(victim)
	if string(got) != "DO NOT TOUCH" {
		t.Fatalf("the beacon wrote through a symlink")
	}
}

// O_NOFOLLOW is what closes the race between the Lstat above and the create.
// Asserted directly against the syscall, with a real symlink.
func TestOpenFlags_RefusesASymlinkAtTheCreatePath(t *testing.T) {
	root := t.TempDir()
	victim := filepath.Join(root, "victim")
	if err := os.WriteFile(victim, []byte("DO NOT TOUCH"), 0o644); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(root, "part")
	if err := os.Symlink(victim, link); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	f, err := os.OpenFile(link, openFlags(), 0o644)
	if err == nil {
		_ = f.Close()
		t.Fatalf("openFlags() followed/overwrote a symlink — O_EXCL|O_NOFOLLOW is not in effect")
	}
	got, _ := os.ReadFile(victim)
	if string(got) != "DO NOT TOUCH" {
		t.Fatalf("the victim file was written through")
	}
}

func TestStage_TokenIsSentWhenConfigured(t *testing.T) {
	var gotAuth atomic.Value
	gotAuth.Store("")
	body := []byte("payload")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth.Store(r.Header.Get("Authorization"))
		_, _ = w.Write(body)
	}))
	defer srv.Close()

	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.SetArtifactToken("  shelf-secret  ")
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "x.sh"),
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	if gotAuth.Load().(string) != "Bearer shelf-secret" {
		t.Fatalf("Authorization = %q", gotAuth.Load())
	}
}

// -----------------------------------------------------------------------------
// 5 · noexec pre-flight (injected, because CI cannot mount noexec unprivileged)
// -----------------------------------------------------------------------------

func TestStage_NoexecMountIsAPreflightFailureNotAMidRunMystery(t *testing.T) {
	if !execProbeSupported() {
		t.Skip("exec probe not applicable on this platform")
	}
	body := []byte("#!/bin/sh\nexit 0\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.artifactProbeExec = func(string) error { return errors.New("permission denied") }

	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: filepath.Join(root, "r", "x.sh"), Mode: "0755",
	})
	f := onlyFailure(t, set)
	if f.Code != codeDestNoexec {
		t.Fatalf("code = %s, want %s", f.Code, codeDestNoexec)
	}
	if !strings.Contains(f.Detail, "CORTEXSIM_STAGE_ROOT") {
		t.Errorf("a noexec failure must name the workaround: %q", f.Detail)
	}
}

func TestStage_NonExecutableArtifactIsNotProbed(t *testing.T) {
	if !execProbeSupported() {
		t.Skip("exec probe not applicable on this platform")
	}
	body := []byte("data")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	probed := false
	c.artifactProbeExec = func(string) error { probed = true; return nil }

	set := stageOneArtifact(t, c, Artifact{
		Name: "wordlist.txt", SHA256: digestOf(body),
		Path: "/api/shelf/payload/wordlist.txt", Dest: filepath.Join(root, "r", "wordlist.txt"), Mode: "0644",
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	if probed {
		t.Fatalf("a 0644 data artifact must not trigger an exec probe")
	}
}

// -----------------------------------------------------------------------------
// 6 · Teardown
// -----------------------------------------------------------------------------

func TestCleanup_RemovesWhatItCreatedAndNothingElse(t *testing.T) {
	body := []byte("payload")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)

	// A pre-existing sibling inside the allowlist that the beacon did not create.
	bystander := filepath.Join(root, "bystander.txt")
	if err := os.WriteFile(bystander, []byte("keep me"), 0o644); err != nil {
		t.Fatal(err)
	}

	dest := filepath.Join(root, "run-1", "nested", "x.sh")
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: dest, Mode: "0755",
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	set.Cleanup(c, "")

	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("staged tooling survived cleanup at %s", dest)
	}
	for _, d := range []string{filepath.Join(root, "run-1", "nested"), filepath.Join(root, "run-1")} {
		if _, err := os.Stat(d); !os.IsNotExist(err) {
			t.Errorf("directory %s created by staging survived cleanup", d)
		}
	}
	if _, err := os.Stat(bystander); err != nil {
		t.Fatalf("cleanup removed a path it did not create: %v", err)
	}
	if _, err := os.Stat(root); err != nil {
		t.Fatalf("cleanup removed a pre-existing directory: %v", err)
	}
}

func TestCleanup_LeavesANonEmptyDirectoryAlone(t *testing.T) {
	body := []byte("payload")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)

	dest := filepath.Join(root, "run-1", "x.sh")
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: dest, Mode: "0755",
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	// Something else drops a file in our staging directory mid-run.
	other := filepath.Join(root, "run-1", "someone-elses-output.txt")
	if err := os.WriteFile(other, []byte("evidence"), 0o644); err != nil {
		t.Fatal(err)
	}
	set.Cleanup(c, "")

	if _, err := os.Stat(dest); !os.IsNotExist(err) {
		t.Fatalf("staged tooling survived cleanup")
	}
	if _, err := os.Stat(other); err != nil {
		t.Fatalf("cleanup used RemoveAll and destroyed a file it did not create: %v", err)
	}
}

func TestCleanup_RemovesTheExecProbe(t *testing.T) {
	if !execProbeSupported() {
		t.Skip("exec probe not applicable on this platform")
	}
	body := []byte("#!/bin/sh\nexit 0\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	c := stagingClient(t, srv.URL, root)
	c.artifactProbeExec = func(string) error { return nil }

	dest := filepath.Join(root, "run-1", "x.sh")
	set := stageOneArtifact(t, c, Artifact{
		Name: "x.sh", SHA256: digestOf(body),
		Path: "/api/shelf/payload/x.sh", Dest: dest, Mode: "0755",
	})
	if !set.OK() {
		t.Fatalf("staging failed: %+v", set.Failures)
	}
	probe := filepath.Join(root, "run-1", execProbeName)
	if _, err := os.Stat(probe); err != nil {
		t.Fatalf("exec probe was not written: %v", err)
	}
	set.Cleanup(c, "")
	if _, err := os.Stat(probe); !os.IsNotExist(err) {
		t.Fatalf("exec probe survived cleanup")
	}
}

func TestCleanup_ReportsResidueIntoTheRunRecord(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", time.Millisecond)

	root := t.TempDir()
	// A file that no longer exists is not residue; a directory standing in for
	// an unremovable file is. Simulate by pointing the ledger at a directory,
	// which os.Remove refuses while it is non-empty.
	stuck := filepath.Join(root, "stuck")
	if err := os.MkdirAll(filepath.Join(stuck, "child"), 0o755); err != nil {
		t.Fatal(err)
	}
	set := &stagedSet{Server: rs.URL, Total: 1, Files: []stagedFile{{
		Artifact: Artifact{Name: "x.sh"}, Dest: stuck,
	}}}
	set.Cleanup(c, "run-9")

	outs := rs.find("POST", "/output")
	if len(outs) == 0 {
		t.Fatal("residue was not reported into the run record")
	}
	if !strings.Contains(outs[0].Body, artifactResidueMarker) {
		t.Fatalf("residue frame missing its marker: %s", outs[0].Body)
	}
	if !strings.Contains(outs[0].Body, "engagement close-out") {
		t.Fatalf("residue frame must tell the DC what to do: %s", outs[0].Body)
	}
}

// -----------------------------------------------------------------------------
// 7 · Stale-staging sweep (the SIGKILL / reboot case)
// -----------------------------------------------------------------------------

func TestSweepStaleStaging(t *testing.T) {
	root := t.TempDir()
	old := time.Now().Add(-48 * time.Hour)

	stale := filepath.Join(root, "7f2a1c3d-1111-2222-3333-444455556666")
	fresh := filepath.Join(root, "aaaabbbb-1111-2222-3333-444455556666")
	// A directory that is NOT a run id. CORTEXSIM_STAGE_ROOT may legitimately be
	// /var/tmp, so a blanket sweep there would delete other people's files.
	notOurs := filepath.Join(root, "some-other-tool")
	for _, d := range []string{stale, fresh, notOurs} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.Chtimes(stale, old, old); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(notOurs, old, old); err != nil {
		t.Fatal(err)
	}

	n := sweepStaleStagingIn(root, time.Now(), 24*time.Hour)
	if n != 1 {
		t.Fatalf("swept %d, want 1", n)
	}
	if _, err := os.Stat(stale); !os.IsNotExist(err) {
		t.Errorf("stale run directory survived the sweep")
	}
	if _, err := os.Stat(fresh); err != nil {
		t.Errorf("a fresh run directory was swept: %v", err)
	}
	if _, err := os.Stat(notOurs); err != nil {
		t.Errorf("the sweep removed a directory that is not a run id: %v", err)
	}
}

func TestSweepStaleStaging_MissingRootIsNotAnError(t *testing.T) {
	if n := sweepStaleStagingIn(filepath.Join(t.TempDir(), "nope"), time.Now(), time.Hour); n != 0 {
		t.Fatalf("swept %d from a nonexistent root", n)
	}
}

// -----------------------------------------------------------------------------
// 8 · The failure frames — the part that must not be cut
// -----------------------------------------------------------------------------

func TestReportStagingFailure_FramesSayTheStepDidNotRun(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", time.Millisecond)

	task := &Task{
		RunID: "run-1", ScenarioID: "SIM-CDR-001",
		Steps: []Step{
			{ID: "step-01", MitreTechnique: "T1059", Identity: "www-data"},
			{ID: "step-05", MitreTechnique: "T1082", Identity: "container-runtime"},
		},
	}
	set := &stagedSet{Server: rs.URL, Total: 2, Failures: []artifactFailure{{
		Artifact: Artifact{Name: "linpeas.sh", Path: "/api/shelf/payload/linpeas.sh",
			Dest: "/tmp/.cache/sysinfo.sh", Steps: []string{"step-05"}},
		Code:   codeSHA256Mismatch,
		Detail: "want=aaa got =bbb",
	}}}
	c.reportStagingFailure(task, set)

	outs := rs.find("POST", "/output")
	if len(outs) < 2 {
		t.Fatalf("want a per-step frame and a summary frame, got %d", len(outs))
	}
	stepFrame := outs[0].Body
	for _, want := range []string{
		"STEP 2/2", "step-05", "T1082", artifactNotStagedMarker, "linpeas.sh",
		codeSHA256Mismatch, "/tmp/.cache/sysinfo.sh", "THIS STEP DID NOT RUN",
		"NOT a gap in the customer", "exit_code=78",
	} {
		if !strings.Contains(stepFrame, want) {
			t.Errorf("step frame missing %q:\n%s", want, stepFrame)
		}
	}
	// The step that needed nothing must NOT get a frame claiming it did not run
	// for an artifact reason — but it must also never be reported as having run.
	if strings.Contains(stepFrame, "step-01") {
		t.Errorf("failure was attributed to a step that did not need the artifact")
	}
	var posted struct {
		Output string `json:"output"`
	}
	if err := json.Unmarshal([]byte(stepFrame), &posted); err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(posted.Output, "\n") {
		t.Errorf("step frame must end with a newline or it runs into the next frame:\n%q", posted.Output)
	}

	comps := rs.find("POST", "/complete")
	if len(comps) != 1 {
		t.Fatalf("want exactly 1 /complete, got %d", len(comps))
	}
	var body struct {
		ExitCode int    `json:"exit_code"`
		Summary  string `json:"summary"`
	}
	if err := json.Unmarshal([]byte(comps[0].Body), &body); err != nil {
		t.Fatal(err)
	}
	// EX_CONFIG — the same "this never ran" code the k8s pod uses, so a DC learns
	// one exit code across both delivery vehicles.
	if body.ExitCode != 78 {
		t.Errorf("exit_code = %d, want 78", body.ExitCode)
	}
	for _, want := range []string{artifactStagingFailedMarker, "NO STEPS RAN", "proves NOTHING", codeSHA256Mismatch} {
		if !strings.Contains(body.Summary, want) {
			t.Errorf("summary missing %q: %s", want, body.Summary)
		}
	}
}

func TestReportStagingFailure_UnattributedArtifactLandsOnStepOne(t *testing.T) {
	rs := newRecordingServer(t, nil)
	defer rs.Close()
	c := New(rs.URL, "a-1", time.Millisecond)

	task := &Task{RunID: "r", ScenarioID: "S", Steps: []Step{{ID: "step-01", MitreTechnique: "T1059"}}}
	set := &stagedSet{Server: rs.URL, Total: 1, Failures: []artifactFailure{{
		Artifact: Artifact{Name: "deepce.sh", Path: "/api/shelf/payload/deepce.sh", Dest: "/tmp/deepce.sh"},
		Code:     codeUnavailable, Detail: "HTTP 404",
	}}}
	c.reportStagingFailure(task, set)

	outs := rs.find("POST", "/output")
	if len(outs) < 2 {
		t.Fatalf("want a step frame and a summary frame, got %d", len(outs))
	}
	if !strings.Contains(outs[0].Body, "STEP 1/1") {
		t.Fatalf("an artifact naming no steps must still be attributed: %s", outs[0].Body)
	}
}

func TestArtifactCache_HitAndPreservation(t *testing.T) {
	body := []byte("#!/bin/sh\necho 'cached tool'\n")
	srv := newShelfServer(t, body)
	root := t.TempDir()
	cacheDir := filepath.Join(root, "cache")
	stageRoot := filepath.Join(root, "staging")

	c := stagingClient(t, srv.URL, stageRoot)
	c.artifactCacheDirFunc = func() string { return cacheDir }

	art := Artifact{
		Name:   "tool.sh",
		SHA256: digestOf(body),
		Path:   "/api/shelf/payload/tool.sh",
		Dest:   filepath.Join(stageRoot, "tool.sh"),
		Mode:   "0755",
	}

	// First staging run: fetches from HTTP server
	set1 := stageOneArtifact(t, c, art)
	if !set1.OK() {
		t.Fatalf("first stage failed: %+v", set1.Failures)
	}
	if n := srv.hits.Load(); n != 1 {
		t.Fatalf("expected 1 HTTP hit on first stage, got %d", n)
	}
	if set1.Files[0].FromCache {
		t.Fatalf("expected FromCache false on initial download")
	}

	// Cleanup should remove dest from staging, but keep the file in cacheDir
	set1.Cleanup(c, "run-1")
	if _, err := os.Stat(art.Dest); !os.IsNotExist(err) {
		t.Fatalf("staged file survived Cleanup at %s", art.Dest)
	}

	cachedFile := filepath.Join(cacheDir, strings.ToLower(art.SHA256))
	if _, err := os.Stat(cachedFile); err != nil {
		t.Fatalf("expected cached file to survive at %s, got err: %v", cachedFile, err)
	}

	// Second staging run: should hit local cache without contacting HTTP server
	set2 := stageOneArtifact(t, c, art)
	if !set2.OK() {
		t.Fatalf("second stage failed: %+v", set2.Failures)
	}
	if n := srv.hits.Load(); n != 1 {
		t.Fatalf("expected 1 HTTP hit after second stage (cache-hit), got %d", n)
	}
	if !set2.Files[0].FromCache {
		t.Fatalf("expected FromCache true on second staging")
	}
	if !strings.Contains(set2.Ledger(), "(cache-hit)") {
		t.Fatalf("expected ledger to indicate cache-hit, got:\n%s", set2.Ledger())
	}

	// Verify staged file content
	gotBytes, err := os.ReadFile(art.Dest)
	if err != nil {
		t.Fatalf("could not read staged dest: %v", err)
	}
	if string(gotBytes) != string(body) {
		t.Fatalf("got %q, want %q", gotBytes, body)
	}
}

// -----------------------------------------------------------------------------
// NOT PROVABLE HERE — stated rather than implied:
//
//   * the Windows junction / reparse-point residual gap against real NTFS
//     (O_EXCL refuses an existing reparse point; a junction created between the
//     Lstat and the create is not closed, and there is no stdlib O_NOFOLLOW);
//   * that a REAL `noexec` mount produces the error the injected prober models;
//   * anything on darwin;
//   * the root -> `runuser -l www-data` permission trap, which needs euid 0 and
//     a real www-data — see TestStagedFileIsExecutableByAnotherUser below, which
//     SKIPS on a laptop and runs inside the prod image.
// -----------------------------------------------------------------------------
