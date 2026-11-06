package main

// Under the Windows service control manager the beacon has no console: stderr
// is a null handle and every log line the beacon writes is discarded. A DC
// whose service "started" but whose agent never appeared in the console had,
// before this sink, nothing on the endpoint to read. These tests pin the sink's
// three contracts — where it writes, that it stays bounded on a customer disk,
// and that it never takes the beacon down when it cannot write.

import (
	"bytes"
	"flag"
	"log"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func readFile(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	return string(b)
}

// TestRotatingFileRotatesAtCap: the write that would cross the cap moves the
// current file to .1 first, so no file ever exceeds the cap when every write
// fits inside it.
func TestRotatingFileRotatesAtCap(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.log")

	r, err := openRotatingFile(path, 100, nil)
	if err != nil {
		t.Fatalf("openRotatingFile: %v", err)
	}
	defer r.Close()

	a := strings.Repeat("a", 59) + "\n"
	b := strings.Repeat("b", 59) + "\n"
	if _, err := r.Write([]byte(a)); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path + ".1"); !os.IsNotExist(err) {
		t.Fatalf("rotated before the cap was reached (stat .1: %v)", err)
	}
	if _, err := r.Write([]byte(b)); err != nil {
		t.Fatal(err)
	}

	if got := readFile(t, path+".1"); got != a {
		t.Errorf("agent.log.1 = %q, want the pre-rotation content %q", got, a)
	}
	if got := readFile(t, path); got != b {
		t.Errorf("agent.log = %q, want only the post-rotation write %q", got, b)
	}
	for _, p := range []string{path, path + ".1"} {
		if fi, err := os.Stat(p); err != nil || fi.Size() > 100 {
			t.Errorf("%s size = %v (err %v), want <= cap 100", p, fi.Size(), err)
		}
	}
}

// TestRotatingFileReplacesOldBackup: there is exactly one backup generation;
// the second rotation replaces .1 rather than failing or accumulating files.
func TestRotatingFileReplacesOldBackup(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.log")
	r, err := openRotatingFile(path, 100, nil)
	if err != nil {
		t.Fatalf("openRotatingFile: %v", err)
	}
	defer r.Close()

	for _, c := range []string{"a", "b", "c"} {
		if _, err := r.Write([]byte(strings.Repeat(c, 79) + "\n")); err != nil {
			t.Fatal(err)
		}
	}
	if got := readFile(t, path+".1"); !strings.HasPrefix(got, "b") {
		t.Errorf("agent.log.1 should hold the second generation (b...), got %q", got)
	}
	if got := readFile(t, path); !strings.HasPrefix(got, "c") {
		t.Errorf("agent.log should hold the newest generation (c...), got %q", got)
	}
	entries, _ := os.ReadDir(dir)
	if len(entries) != 2 {
		names := []string{}
		for _, e := range entries {
			names = append(names, e.Name())
		}
		t.Errorf("want exactly agent.log + agent.log.1, got %v", names)
	}
}

// TestRotatingFileCountsPreexistingSize: a service restart appends to the log
// it left behind, and the cap counts what is already on disk — otherwise every
// restart would grant the file a fresh 10 MiB.
func TestRotatingFileCountsPreexistingSize(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.log")
	old := strings.Repeat("o", 90)
	if err := os.WriteFile(path, []byte(old), 0o644); err != nil {
		t.Fatal(err)
	}
	r, err := openRotatingFile(path, 100, nil)
	if err != nil {
		t.Fatalf("openRotatingFile: %v", err)
	}
	defer r.Close()
	if _, err := r.Write([]byte(strings.Repeat("n", 20))); err != nil {
		t.Fatal(err)
	}
	if got := readFile(t, path+".1"); got != old {
		t.Errorf("pre-existing content should have rotated to .1, got %q", got)
	}
}

// TestRotatingFileKeepsCapWhenRenameFails: on Windows a reader holding
// agent.log.1 open (Get-Content -Wait, an editor) makes the rename fail. The
// cap is the property a customer endpoint needs, so the sink truncates in place
// and says so in the file, rather than growing without bound.
func TestRotatingFileKeepsCapWhenRenameFails(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "agent.log")
	// A non-empty directory where the backup should go: no platform can rename
	// a file over it, which is a portable stand-in for a locked backup.
	if err := os.MkdirAll(filepath.Join(path+".1", "held"), 0o755); err != nil {
		t.Fatal(err)
	}
	r, err := openRotatingFile(path, 400, nil)
	if err != nil {
		t.Fatalf("openRotatingFile: %v", err)
	}
	defer r.Close()

	for i := 0; i < 20; i++ {
		if _, err := r.Write([]byte(strings.Repeat("x", 49) + "\n")); err != nil {
			t.Fatalf("write %d: %v", i, err)
		}
	}
	fi, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Size() > 400 {
		t.Errorf("agent.log grew to %d bytes past the 400-byte cap after a failed rename", fi.Size())
	}
	if got := readFile(t, path); !strings.Contains(got, "could not rotate") {
		t.Errorf("a failed rotation must leave a note in the log, got %q", got)
	}
}

// TestResolveLogPathPrecedence: flag beats env beats the service default, and a
// console run with neither keeps logging to stderr exactly as before.
func TestResolveLogPathPrecedence(t *testing.T) {
	const pd = `D:\ProgramData`
	cases := []struct {
		name      string
		flag, env string
		scm       bool
		want      string
	}{
		{"flag wins over env and SCM default", "/f.log", "/e.log", true, "/f.log"},
		{"env wins over SCM default", "", "/e.log", true, "/e.log"},
		{"env honoured on a console run too", "", "/e.log", false, "/e.log"},
		{"SCM default when neither is set", "", "", true, `D:\ProgramData\CortexSim\agent.log`},
		{"console default is stderr (empty)", "", "", false, ""},
		{"whitespace-only flag is unset", "   ", "", false, ""},
		{"whitespace-only env is unset", "", " \t", true, `D:\ProgramData\CortexSim\agent.log`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := resolveLogPath(tc.flag, tc.env, tc.scm, pd); got != tc.want {
				t.Errorf("resolveLogPath(%q, %q, scm=%t) = %q, want %q", tc.flag, tc.env, tc.scm, got, tc.want)
			}
		})
	}
}

// TestLogPathReadsAgentEnvNotSimCoreEnv: CORTEXSIM_LOG_FILE is SimCore's own
// setting (core/config.py) and is commonly exported on a DC's box. If the
// beacon honoured it, a beacon started from the same shell would write into
// SimCore's log path. Only CORTEXSIM_AGENT_LOG_FILE moves the beacon's log.
func TestLogPathReadsAgentEnvNotSimCoreEnv(t *testing.T) {
	t.Setenv("CORTEXSIM_LOG_FILE", "logs/cortexsim.log")
	t.Setenv("CORTEXSIM_AGENT_LOG_FILE", "")
	if got := logPathFor("", false, os.Getenv); got != "" {
		t.Errorf("CORTEXSIM_LOG_FILE leaked into the beacon log path: %q", got)
	}
	t.Setenv("CORTEXSIM_AGENT_LOG_FILE", "/var/tmp/beacon.log")
	if got := logPathFor("", false, os.Getenv); got != "/var/tmp/beacon.log" {
		t.Errorf("CORTEXSIM_AGENT_LOG_FILE not honoured: got %q", got)
	}
	if got := logPathFor("/flag.log", false, os.Getenv); got != "/flag.log" {
		t.Errorf("--log-file must beat CORTEXSIM_AGENT_LOG_FILE: got %q", got)
	}
}

// TestDefaultServiceLogPathIsByteIdentical: the Windows installer prints
// %ProgramData%\CortexSim\agent.log as the place to look and does NOT pass
// --log-file, so the beacon's default must be that exact string.
func TestDefaultServiceLogPathIsByteIdentical(t *testing.T) {
	cases := map[string]string{
		`C:\ProgramData`:   `C:\ProgramData\CortexSim\agent.log`,
		`C:\ProgramData\`:  `C:\ProgramData\CortexSim\agent.log`,
		` C:\ProgramData `: `C:\ProgramData\CortexSim\agent.log`,
		`E:\Data`:          `E:\Data\CortexSim\agent.log`,
		"":                 `C:\ProgramData\CortexSim\agent.log`,
	}
	for in, want := range cases {
		if got := defaultServiceLogPath(in); got != want {
			t.Errorf("defaultServiceLogPath(%q) = %q, want %q", in, got, want)
		}
	}
	if logRotateBytes != 10<<20 {
		t.Errorf("logRotateBytes = %d, want 10 MiB", logRotateBytes)
	}
}

// TestInstallLogSinkWritesAndAnnouncesPath: the sink logs its own path as its
// first line, so the file says where it is and the console says where to look.
func TestInstallLogSinkWritesAndAnnouncesPath(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", "agent.log")
	var stderr bytes.Buffer
	l := log.New(&stderr, "", 0)

	closeFn := installLogSink(l, path, 1<<20, &stderr)
	l.Printf("hello from the beacon")
	closeFn()

	got := readFile(t, path)
	if !strings.Contains(got, path) {
		t.Errorf("log file does not name its own path; content %q", got)
	}
	if !strings.Contains(got, "hello from the beacon") {
		t.Errorf("log line did not reach the file; content %q", got)
	}
	if !strings.Contains(stderr.String(), path) {
		t.Errorf("stderr was not told where the log went: %q", stderr.String())
	}
	if strings.Contains(stderr.String(), "hello from the beacon") {
		t.Errorf("log line went to stderr instead of the file: %q", stderr.String())
	}
}

// TestInstallLogSinkFallsBackToStderr: an unwritable log path must never stop
// the beacon. It keeps logging to the fallback and says why.
func TestInstallLogSinkFallsBackToStderr(t *testing.T) {
	dir := t.TempDir()
	blocker := filepath.Join(dir, "not-a-dir")
	if err := os.WriteFile(blocker, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(blocker, "agent.log") // parent is a regular file

	var stderr bytes.Buffer
	l := log.New(&stderr, "", 0)
	closeFn := installLogSink(l, path, 1<<20, &stderr)
	l.Printf("still running")
	closeFn()

	out := stderr.String()
	if !strings.Contains(out, "still running") {
		t.Errorf("logging stopped after the sink failed to open: %q", out)
	}
	if !strings.Contains(out, path) || !strings.Contains(strings.ToLower(out), "stderr") {
		t.Errorf("fallback must name the path it could not open and that it is using stderr: %q", out)
	}
}

// TestRegisterLogFileFlag: the flag is spelled --log-file and defaults to
// empty (stderr), so an upgraded console or systemd beacon logs exactly where
// it did before.
func TestRegisterLogFileFlag(t *testing.T) {
	fs := flag.NewFlagSet("t", flag.ContinueOnError)
	v := registerLogFileFlag(fs)
	if *v != "" {
		t.Errorf("--log-file default = %q, want empty (stderr)", *v)
	}
	if err := fs.Parse([]string{"--log-file", "/tmp/a.log"}); err != nil {
		t.Fatalf("parse: %v", err)
	}
	if *v != "/tmp/a.log" {
		t.Errorf("--log-file parsed as %q", *v)
	}
}
