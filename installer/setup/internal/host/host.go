// Package host is the setup program's only door to the machine: commands,
// files and local HTTP probes. Steps take a Host so tests can hand them a Fake
// and never touch the real system.
package host

import (
	"bytes"
	"context"
	"io"
	"io/fs"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// Host is everything a step may do to, or learn from, the machine.
type Host interface {
	// Run executes name with args and returns what it wrote. err is non-nil
	// when the command could not start or exited non-zero (*exec.ExitError).
	Run(ctx context.Context, name string, args ...string) (stdout, stderr []byte, err error)
	ReadFile(name string) ([]byte, error)
	// WriteFile replaces name with data and leaves it with exactly perm,
	// whatever mode an earlier file had.
	WriteFile(name string, data []byte, perm fs.FileMode) error
	MkdirAll(path string, perm fs.FileMode) error
	RemoveAll(path string) error
	Stat(name string) (fs.FileInfo, error)
	LookPath(file string) (string, error)
	// HTTPGet fetches url directly (never through a proxy) and returns the
	// status code and body.
	HTTPGet(ctx context.Context, url string) (status int, body []byte, err error)
}

const (
	// httpTimeout bounds one HTTPGet, connection to last byte.
	httpTimeout = 10 * time.Second
	// maxHTTPBody caps what HTTPGet reads; /api/health answers in kilobytes.
	maxHTTPBody = 4 << 20
)

// OS returns the Host backed by the real operating system.
func OS() Host {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	// The installer only probes this machine's own SimCore; an egress proxy
	// from the environment must not carry those requests.
	transport.Proxy = nil
	return osHost{client: &http.Client{Timeout: httpTimeout, Transport: transport}}
}

type osHost struct {
	client *http.Client
}

func (osHost) Run(ctx context.Context, name string, args ...string) ([]byte, []byte, error) {
	var stdout, stderr bytes.Buffer
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	return stdout.Bytes(), stderr.Bytes(), err
}

func (osHost) ReadFile(name string) ([]byte, error) { return os.ReadFile(name) }

// WriteFile writes to a temporary file in the same directory, sets perm on
// it, and renames it over name. A crash mid-write leaves the old file whole,
// and the result has exactly perm: os.WriteFile keeps an existing file's mode,
// which would leave a 0644 secret file at 0644.
func (osHost) WriteFile(name string, data []byte, perm fs.FileMode) (err error) {
	f, err := os.CreateTemp(filepath.Dir(name), "."+filepath.Base(name)+".tmp-*")
	if err != nil {
		return err
	}
	tmp := f.Name()
	defer func() {
		if err != nil {
			_ = os.Remove(tmp)
		}
	}()
	if _, err = f.Write(data); err != nil {
		_ = f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		_ = f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = os.Chmod(tmp, perm); err != nil {
		return err
	}
	return os.Rename(tmp, name)
}

func (osHost) MkdirAll(path string, perm fs.FileMode) error { return os.MkdirAll(path, perm) }

func (osHost) RemoveAll(path string) error { return os.RemoveAll(path) }

func (osHost) Stat(name string) (fs.FileInfo, error) { return os.Stat(name) }

func (osHost) LookPath(file string) (string, error) { return exec.LookPath(file) }

func (h osHost) HTTPGet(ctx context.Context, url string) (int, []byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, nil, err
	}
	resp, err := h.client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxHTTPBody))
	return resp.StatusCode, body, err
}
