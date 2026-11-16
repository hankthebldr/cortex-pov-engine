package host

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os/exec"
	"path"
	"strings"
	"sync"
	"time"
)

// ErrNoFakeResponse is returned (wrapped) by Fake.Run and Fake.HTTPGet when no
// response is configured for the request, so a test sees a command it did not
// expect instead of a silent empty answer.
var ErrNoFakeResponse = errors.New("host.Fake: no response configured")

// FakeResponse is a canned answer. Run uses Stdout, Stderr and Err; HTTPGet
// uses Status, Body and Err.
type FakeResponse struct {
	Stdout, Stderr []byte
	Status         int
	Body           []byte
	Err            error
}

// Call records one method call on a Fake.
type Call struct {
	Method string      // "Run", "ReadFile", "WriteFile", "MkdirAll", "RemoveAll", "Stat", "LookPath" or "HTTPGet"
	Args   []string    // Run: the command and its arguments; HTTPGet: the URL; otherwise the path or name
	Mode   fs.FileMode // WriteFile and MkdirAll
	Data   []byte      // WriteFile
}

// Fake is a deterministic in-memory Host for tests.
//
//   - Files holds file contents by path. WriteFile and RemoveAll change it.
//   - Responses answers Run, keyed by the command line joined with single
//     spaces ("systemctl is-active povengine.service"), and HTTPGet, keyed by
//     the URL. A request with no entry returns ErrNoFakeResponse.
//   - Paths answers LookPath (name -> full path); a missing name returns an
//     *exec.Error wrapping exec.ErrNotFound, as the real one does.
//   - Calls records every method call in order.
//
// A path is a directory once MkdirAll created it or a file in Files lies
// beneath it. Both "/" and `\` separate path elements, so Windows paths work
// in tests that run on Linux.
type Fake struct {
	Files     map[string][]byte
	Responses map[string]FakeResponse
	Paths     map[string]string
	Calls     []Call

	mu    sync.Mutex
	modes map[string]fs.FileMode
	dirs  map[string]fs.FileMode
}

var _ Host = (*Fake)(nil)

func (f *Fake) record(c Call) {
	f.Calls = append(f.Calls, c)
}

func (f *Fake) Run(ctx context.Context, name string, args ...string) ([]byte, []byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	argv := append([]string{name}, args...)
	f.record(Call{Method: "Run", Args: argv})
	if err := ctx.Err(); err != nil {
		return nil, nil, err
	}
	key := strings.Join(argv, " ")
	r, ok := f.Responses[key]
	if !ok {
		return nil, nil, fmt.Errorf("%w: run %q", ErrNoFakeResponse, key)
	}
	return clone(r.Stdout), clone(r.Stderr), r.Err
}

func (f *Fake) ReadFile(name string) ([]byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(Call{Method: "ReadFile", Args: []string{name}})
	b, ok := f.Files[name]
	if !ok {
		return nil, &fs.PathError{Op: "open", Path: name, Err: fs.ErrNotExist}
	}
	return clone(b), nil
}

func (f *Fake) WriteFile(name string, data []byte, perm fs.FileMode) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(Call{Method: "WriteFile", Args: []string{name}, Mode: perm, Data: clone(data)})
	if f.Files == nil {
		f.Files = map[string][]byte{}
	}
	if f.modes == nil {
		f.modes = map[string]fs.FileMode{}
	}
	f.Files[name] = clone(data)
	f.modes[name] = perm
	return nil
}

func (f *Fake) MkdirAll(p string, perm fs.FileMode) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(Call{Method: "MkdirAll", Args: []string{p}, Mode: perm})
	if f.dirs == nil {
		f.dirs = map[string]fs.FileMode{}
	}
	if _, ok := f.dirs[p]; !ok {
		f.dirs[p] = perm
	}
	return nil
}

func (f *Fake) RemoveAll(p string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(Call{Method: "RemoveAll", Args: []string{p}})
	for k := range f.Files {
		if k == p || under(k, p) {
			delete(f.Files, k)
			delete(f.modes, k)
		}
	}
	for k := range f.dirs {
		if k == p || under(k, p) {
			delete(f.dirs, k)
		}
	}
	return nil
}

func (f *Fake) Stat(name string) (fs.FileInfo, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(Call{Method: "Stat", Args: []string{name}})
	if b, ok := f.Files[name]; ok {
		mode, ok := f.modes[name]
		if !ok {
			mode = 0o644
		}
		return fakeInfo{name: base(name), size: int64(len(b)), mode: mode}, nil
	}
	if mode, ok := f.dirs[name]; ok {
		return fakeInfo{name: base(name), mode: fs.ModeDir | mode}, nil
	}
	for k := range f.Files {
		if under(k, name) {
			return fakeInfo{name: base(name), mode: fs.ModeDir | 0o755}, nil
		}
	}
	for k := range f.dirs {
		if under(k, name) {
			return fakeInfo{name: base(name), mode: fs.ModeDir | 0o755}, nil
		}
	}
	return nil, &fs.PathError{Op: "stat", Path: name, Err: fs.ErrNotExist}
}

func (f *Fake) LookPath(file string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(Call{Method: "LookPath", Args: []string{file}})
	if p, ok := f.Paths[file]; ok {
		return p, nil
	}
	return "", &exec.Error{Name: file, Err: exec.ErrNotFound}
}

func (f *Fake) HTTPGet(ctx context.Context, url string) (int, []byte, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(Call{Method: "HTTPGet", Args: []string{url}})
	if err := ctx.Err(); err != nil {
		return 0, nil, err
	}
	r, ok := f.Responses[url]
	if !ok {
		return 0, nil, fmt.Errorf("%w: GET %s", ErrNoFakeResponse, url)
	}
	return r.Status, clone(r.Body), r.Err
}

// under reports whether p lies strictly beneath dir.
func under(p, dir string) bool {
	return strings.HasPrefix(p, strings.TrimRight(dir, `/\`)+"/") ||
		strings.HasPrefix(p, strings.TrimRight(dir, `/\`)+`\`)
}

func base(p string) string {
	if i := strings.LastIndexAny(p, `/\`); i >= 0 && i < len(p)-1 {
		return p[i+1:]
	}
	return path.Base(p)
}

func clone(b []byte) []byte {
	if b == nil {
		return nil
	}
	return append([]byte(nil), b...)
}

type fakeInfo struct {
	name string
	size int64
	mode fs.FileMode
}

func (i fakeInfo) Name() string       { return i.name }
func (i fakeInfo) Size() int64        { return i.size }
func (i fakeInfo) Mode() fs.FileMode  { return i.mode }
func (i fakeInfo) ModTime() time.Time { return time.Time{} }
func (i fakeInfo) IsDir() bool        { return i.mode.IsDir() }
func (i fakeInfo) Sys() any           { return nil }
