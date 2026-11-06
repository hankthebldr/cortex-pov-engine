package main

// The beacon's log sink.
//
// By default the beacon logs to stderr, which is right for a console run and
// for systemd/launchd (both capture it). Under the Windows service control
// manager it is wrong: a LocalSystem service has no console, stderr is a null
// handle, and every line — including "registration failed" — is discarded. A
// DC whose service "started" but whose agent never appeared had nothing on the
// endpoint to read. So:
//
//   - --log-file / CORTEXSIM_AGENT_LOG_FILE sends the log to a file, on any
//     platform, in console or service mode.
//   - Under the SCM with neither set, the log goes to
//     %ProgramData%\CortexSim\agent.log — the exact path the Windows
//     installer prints (it creates that directory and does NOT pass the flag,
//     so this default must stay byte-identical to what it prints).
//
// The env var is deliberately CORTEXSIM_AGENT_LOG_FILE and NOT
// CORTEXSIM_LOG_FILE: the latter is SimCore's own setting (core/config.py) and
// is often exported on the box a DC runs both from.
//
// The file is size-capped: at logRotateBytes it is renamed to <path>.1
// (replacing any previous .1) and reopened, so a beacon left running for a
// whole POV on a customer endpoint holds at most two caps of disk. If the
// file cannot be opened the beacon keeps running and keeps logging to stderr
// — a log destination is never a reason to stop the beacon.

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

const (
	agentLogFileEnv  = "CORTEXSIM_AGENT_LOG_FILE"
	logFileFlagName  = "log-file"
	logRotateBytes   = 10 << 20 // 10 MiB per generation; one .1 generation is kept
	serviceLogSubdir = `CortexSim\agent.log`
)

// registerLogFileFlag defines --log-file on fs.
func registerLogFileFlag(fs *flag.FlagSet) *string {
	return fs.String(logFileFlagName, "",
		"Write the beacon log to this file instead of stderr, rotating to <file>.1 at 10 MiB "+
			"(env: "+agentLogFileEnv+"). Under the Windows service control manager the default is "+
			`%ProgramData%\CortexSim\agent.log`+".")
}

// defaultServiceLogPath is %ProgramData%\CortexSim\agent.log, built with a
// literal backslash rather than filepath.Join so the value is the same string
// on every platform that tests it.
func defaultServiceLogPath(programData string) string {
	pd := strings.TrimRight(strings.TrimSpace(programData), `\/`)
	if pd == "" {
		pd = `C:\ProgramData`
	}
	return pd + `\` + serviceLogSubdir
}

// resolveLogPath picks the log destination: --log-file, then
// CORTEXSIM_AGENT_LOG_FILE, then (only under the SCM) the service default.
// "" means stderr.
func resolveLogPath(flagVal, envVal string, underSCM bool, programData string) string {
	if v := strings.TrimSpace(flagVal); v != "" {
		return v
	}
	if v := strings.TrimSpace(envVal); v != "" {
		return v
	}
	if underSCM {
		return defaultServiceLogPath(programData)
	}
	return ""
}

// logPathFor is resolveLogPath fed from the environment.
func logPathFor(flagVal string, underSCM bool, getenv func(string) string) string {
	return resolveLogPath(flagVal, getenv(agentLogFileEnv), underSCM, getenv("ProgramData"))
}

// rotatingFile is an append-only, size-capped log file.
type rotatingFile struct {
	mu       sync.Mutex
	path     string
	max      int64
	f        *os.File
	size     int64
	closed   bool      // after Close, writes go to fallback instead of reopening
	fallback io.Writer // where writes go while the file cannot be (re)opened
}

// openRotatingFile opens (creating the parent directory if needed) path for
// append. The cap counts what is already on disk, so a restart does not grant
// the file a fresh allowance.
func openRotatingFile(path string, maxBytes int64, fallback io.Writer) (*rotatingFile, error) {
	r := &rotatingFile{path: path, max: maxBytes, fallback: fallback}
	if err := r.reopenLocked(false); err != nil {
		return nil, err
	}
	return r, nil
}

// reopenLocked opens the file for append, or — truncate — empties it. The
// truncating open deliberately does NOT add O_APPEND: on Windows, O_APPEND
// strips GENERIC_WRITE from the handle, and only newer toolchains' syscall.Open
// keep it back for O_TRUNC (CI builds with Go 1.21, the image with 1.22). A
// freshly truncated file with one writer appends by construction anyway, so
// the pair is avoided rather than left toolchain-dependent.
func (r *rotatingFile) reopenLocked(truncate bool) error {
	if err := os.MkdirAll(filepath.Dir(r.path), 0o755); err != nil {
		return err
	}
	flags := os.O_WRONLY | os.O_CREATE | os.O_APPEND
	if truncate {
		flags = os.O_WRONLY | os.O_CREATE | os.O_TRUNC
	}
	f, err := os.OpenFile(r.path, flags, 0o644)
	if err != nil {
		return err
	}
	fi, err := f.Stat()
	if err != nil {
		_ = f.Close()
		return err
	}
	r.f, r.size = f, fi.Size()
	return nil
}

// rotateLocked moves the current file to <path>.1 and starts a new one. The
// handle is closed first because Windows refuses to rename a file this
// process still holds open. When the rename fails anyway — another process
// holding the .1 or the current file open, which on Windows blocks a rename —
// the file is truncated in place instead: the cap is the property a customer
// endpoint needs, and the note written into the new file says what was lost.
func (r *rotatingFile) rotateLocked() {
	_ = r.f.Close()
	r.f = nil
	renameErr := os.Rename(r.path, r.path+".1")
	if err := r.reopenLocked(renameErr != nil); err != nil {
		return // Write retries the open and uses the fallback meanwhile
	}
	if renameErr != nil {
		// Kept short and free of full paths so the note itself can never
		// push a small cap over: the file's own path is its first line anyway.
		reason := renameErr
		var le *os.LinkError
		if errors.As(renameErr, &le) {
			reason = le.Err
		}
		note := fmt.Sprintf("[logsink] could not rotate to %s.1 (%v) — truncated in place to keep the %s cap\n",
			filepath.Base(r.path), reason, sizeLabel(r.max))
		if int64(len(note)) < r.max/2 {
			n, _ := io.WriteString(r.f, note)
			r.size += int64(n)
		}
	}
}

// sizeLabel renders a cap as "10 MiB" when it is a whole number of MiB.
func sizeLabel(n int64) string {
	if n >= 1<<20 && n%(1<<20) == 0 {
		return fmt.Sprintf("%d MiB", n>>20)
	}
	return fmt.Sprintf("%d-byte", n)
}

func (r *rotatingFile) Write(p []byte) (int, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		if r.fallback != nil {
			return r.fallback.Write(p)
		}
		return 0, os.ErrClosed
	}
	if r.f != nil && r.size > 0 && r.size+int64(len(p)) > r.max {
		r.rotateLocked()
	}
	if r.f == nil {
		if err := r.reopenLocked(false); err != nil {
			if r.fallback != nil {
				return r.fallback.Write(p)
			}
			return 0, err
		}
	}
	n, err := r.f.Write(p)
	r.size += int64(n)
	return n, err
}

func (r *rotatingFile) Close() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.closed = true
	if r.f == nil {
		return nil
	}
	err := r.f.Close()
	r.f = nil
	return err
}

// installLogSink points l at a size-capped file at path and logs that path as
// this run's first line, telling fallback (stderr) once where the log went. If
// the file cannot be opened, l keeps writing to fallback and the reason is
// logged there. The returned func closes the file.
func installLogSink(l *log.Logger, path string, maxBytes int64, fallback io.Writer) func() {
	if fallback == nil {
		fallback = io.Discard
	}
	r, err := openRotatingFile(path, maxBytes, fallback)
	if err != nil {
		l.SetOutput(fallback)
		l.Printf("WARNING: cannot open log file %s: %v — logging to stderr instead", path, err)
		return func() {}
	}
	if fallback != nil {
		fmt.Fprintf(fallback, "[cortexsim-agent] logging to %s (rotates to %s.1 at %s)\n", path, path, sizeLabel(maxBytes))
	}
	l.SetOutput(r)
	l.Printf("log sink: %s (rotates to %s.1 at %s; pid %d)", path, path, sizeLabel(maxBytes), os.Getpid())
	return func() { _ = r.Close() }
}
