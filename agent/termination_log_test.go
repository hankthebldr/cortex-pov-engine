package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// A pod that crash-looped on a 422 at enrollment exited 1 with no termination
// message, so `kubectl describe pod` showed no cause (kind run, D2). Every fatal
// startup exit now also writes its one FATAL line to the termination log.

func TestWriteTerminationLogWritesAnExistingFileOnly(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "termination-log")
	if err := os.WriteFile(path, []byte("stale and much longer than the new line\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	writeTerminationLog(path, "FATAL X: y")
	if b, _ := os.ReadFile(path); string(b) != "FATAL X: y\n" {
		t.Fatalf("termination log = %q", b)
	}

	// Off Kubernetes there is no /dev/termination-log: never create one.
	absent := filepath.Join(dir, "absent")
	writeTerminationLog(absent, "FATAL X: y")
	if _, err := os.Stat(absent); !os.IsNotExist(err) {
		t.Fatalf("created %s: %v", absent, err)
	}
	// Best effort: an unusable path is ignored, never fatal.
	writeTerminationLog(filepath.Join(dir, "no", "such", "dir"), "FATAL X: y")
}

func TestFatalLineRedactsTokensAndIsBounded(t *testing.T) {
	line := fatalLine("ENROLL_FAILED", "server said cxs_AbC-12_xyz is bad "+strings.Repeat("z", 5000))
	if strings.Contains(line, "AbC-12_xyz") {
		t.Fatalf("token leaked: %q", line[:80])
	}
	if !strings.HasPrefix(line, "FATAL ENROLL_FAILED: server said cxs_") || len(line) > maxTerminationLine {
		t.Fatalf("line = %q (len %d)", line[:60], len(line))
	}
}

// runMain re-executes this test binary as the agent so the real main() and its
// os.Exit paths are observed, with the termination log pointed at a temp file.
func runMain(t *testing.T, args string) (termlog string, out string, code int) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "termination-log")
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(os.Args[0], "-test.run=TestMainFlagValidationExits")
	cmd.Env = append(os.Environ(), "CORTEXSIM_TEST_MAIN_ARGS="+args, "CORTEXSIM_TERMLOG="+path)
	b, err := cmd.CombinedOutput()
	code = 0
	if ee, ok := err.(*exec.ExitError); ok {
		code = ee.ExitCode()
	}
	tl, _ := os.ReadFile(path)
	return string(tl), string(b), code
}

func TestFatalStartupExitsWriteTheTerminationLog(t *testing.T) {
	dir := t.TempDir()
	emptyToken := filepath.Join(dir, "empty-token")
	os.WriteFile(emptyToken, []byte("  \n"), 0o600)
	token := filepath.Join(dir, "token")
	os.WriteFile(token, []byte("cxs_SeCrEt123\n"), 0o600)
	notADir := filepath.Join(dir, "file")
	os.WriteFile(notADir, []byte("x"), 0o600)

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(422)
		w.Write([]byte(`{"error":"interpreters: Input should be a valid list","code":"VALIDATION_ERROR"}`))
	}))
	defer srv.Close()

	cases := map[string]struct{ args, want string }{
		"bad expiry": {"--id x --expires-at not-a-time", "FATAL BAD_EXPIRES_AT: "},
		"empty token": {"--enroll-token-file " + emptyToken + " --state-dir " + filepath.Join(dir, "s1"),
			"FATAL ENROLL_TOKEN_EMPTY: "},
		"state dir unwritable": {"--enroll-token-file " + token + " --state-dir " + filepath.Join(notADir, "s"),
			"FATAL STATE_DIR_UNWRITABLE: "},
		"enroll refused": {"--server " + srv.URL + " --enroll-token-file " + token + " --state-dir " + filepath.Join(dir, "s2"),
			"FATAL VALIDATION_ERROR: "},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			tl, out, code := runMain(t, c.args)
			if code != 1 {
				t.Fatalf("exit = %d, want 1\n%s", code, out)
			}
			if !strings.HasPrefix(tl, c.want) || strings.Count(tl, "\n") != 1 {
				t.Fatalf("termination log = %q, want one line starting %q\n%s", tl, c.want, out)
			}
			if strings.Contains(tl, "SeCrEt123") {
				t.Fatalf("token in termination log: %q", tl)
			}
		})
	}
}
