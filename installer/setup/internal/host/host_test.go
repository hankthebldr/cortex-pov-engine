package host

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"sync/atomic"
	"testing"
	"time"
)

// A cancelled step must give control back even when the command it ran left a
// grandchild holding stdout (a shell wrapper around dockerd, msiexec, ...).
// Without a wait delay, Run blocks until that grandchild exits.
func TestOSRun_CancelReturnsWhileAGrandchildHoldsStdout(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("uses sh and sleep")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	start := time.Now()
	_, _, err := OS().Run(ctx, "sh", "-c", "sleep 30 & sleep 30")
	elapsed := time.Since(start)

	if err == nil {
		t.Error("Run returned no error for a command cancelled mid-flight")
	}
	if limit := runWaitDelay + 5*time.Second; elapsed > limit {
		t.Errorf("Run returned %v after cancellation, want under %v", elapsed.Round(time.Millisecond), limit)
	}
}

func TestOSWriteFile_OverwriteLeavesExactlyPerm(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits")
	}
	dir := t.TempDir()
	p := filepath.Join(dir, "simcore.env")
	if err := os.WriteFile(p, []byte("OLD=1\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	if err := OS().WriteFile(p, []byte("NEW=1\n"), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	fi, err := os.Stat(p)
	if err != nil {
		t.Fatal(err)
	}
	if got := fi.Mode().Perm(); got != 0o600 {
		t.Errorf("mode = %v, want -rw------- (an existing 0644 file must not keep its mode)", got)
	}
	if b, _ := os.ReadFile(p); string(b) != "NEW=1\n" {
		t.Errorf("content = %q, want the new bytes", b)
	}
	if ents, _ := os.ReadDir(dir); len(ents) != 1 {
		t.Errorf("directory holds %d entries, want 1: a temporary file was left behind", len(ents))
	}
}

// HTTPGet probes this machine's own SimCore and must never go through an
// egress proxy from the environment. Go never proxies a loopback URL, so the
// target is a non-loopback documentation address (RFC 5737); the "proxy" is a
// local server that would answer if the request reached it.
func TestOSHTTPGet_IgnoresTheEnvironmentProxy(t *testing.T) {
	var proxied atomic.Int32
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		proxied.Add(1)
		_, _ = w.Write([]byte("answered by the proxy"))
	}))
	defer proxy.Close()
	t.Setenv("HTTP_PROXY", proxy.URL)
	t.Setenv("http_proxy", proxy.URL)
	t.Setenv("NO_PROXY", "")
	t.Setenv("no_proxy", "")

	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()
	status, body, err := OS().HTTPGet(ctx, "http://192.0.2.10:8888/api/health")

	if n := proxied.Load(); n != 0 {
		t.Fatalf("the request went through HTTP_PROXY (%d hit(s); got %d %q)", n, status, body)
	}
	if err == nil {
		t.Errorf("HTTPGet to an unroutable address returned %d %q and no error", status, body)
	}
}
