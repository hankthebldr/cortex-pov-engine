package main

// The beacon's Windows support regressed silently once already: 71 of the 162
// scenarios declare `platforms: [windows]`, yet `GOOS=windows go build ./...`
// failed on POSIX-only syscalls, so pull mode was impossible on Windows and
// nothing in the test suite noticed. CI now gates the cross-compile, but a gate
// that only exists in a YAML file is invisible to anyone running `go test`
// locally — this keeps the guard inside the suite that a developer actually
// runs before pushing.
//
// It shells out to the toolchain rather than asserting on source text because
// the failure mode is a COMPILE error (an unknown struct field, an undefined
// symbol behind a build tag), which no amount of reading Go source from Go can
// detect.

import (
	"context"
	"os/exec"
	"strings"
	"testing"
	"time"
)

func crossBuild(t *testing.T, goos, subcommand string) {
	t.Helper()

	if _, err := exec.LookPath("go"); err != nil {
		t.Skipf("no go toolchain on PATH: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()

	cmd := exec.CommandContext(ctx, "go", subcommand, "./...")
	// CGO_ENABLED=0 mirrors scripts/build-agent-dist.sh: the beacon must be a
	// static, dependency-free binary a DC can copy onto an air-gapped host.
	cmd.Env = append(cmd.Environ(), "GOOS="+goos, "CGO_ENABLED=0")

	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("GOOS=%s go %s ./... failed — the beacon does not support %s:\n%v\n%s",
			goos, subcommand, goos, err, strings.TrimSpace(string(out)))
	}
}

// PULL MODE ON WINDOWS. Without the platform split in agent/executor this fails
// with `unknown field Setpgid` and `undefined: syscall.Kill`.
func TestCrossCompile_Windows(t *testing.T) {
	if testing.Short() {
		t.Skip("cross-compile gate skipped under -short")
	}
	crossBuild(t, "windows", "build")
	crossBuild(t, "windows", "vet")
}

// darwin and linux are the platforms the beacon already shipped on; the Windows
// work must not cost either of them.
func TestCrossCompile_Darwin(t *testing.T) {
	if testing.Short() {
		t.Skip("cross-compile gate skipped under -short")
	}
	crossBuild(t, "darwin", "build")
}

func TestCrossCompile_Linux(t *testing.T) {
	if testing.Short() {
		t.Skip("cross-compile gate skipped under -short")
	}
	crossBuild(t, "linux", "build")
}
