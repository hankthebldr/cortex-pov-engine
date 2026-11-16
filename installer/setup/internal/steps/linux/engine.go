package linux

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

const (
	engineWait  = 30 * time.Second // for the engine to answer after its start
	engineEvery = 1 * time.Second
	// pollTimeout bounds one probe inside a wait loop.
	pollTimeout = 5 * time.Second
	// unitTimeout bounds a systemctl start or restart: it returns only when
	// the unit is up (Type=notify) or has failed.
	unitTimeout = 3 * time.Minute
	// extractTimeout bounds unpacking the engine archive.
	extractTimeout = 2 * time.Minute
	// loadTimeout bounds `docker load` of the SimCore image (a few GiB).
	loadTimeout = 20 * time.Minute

	unitMode = 0o644
)

// Clock is how a step that waits tells time. Tests replace both functions so
// they never sleep; a nil field uses the real clock.
type Clock struct {
	Now   func() time.Time
	Sleep func(time.Duration)
}

func realClock() Clock { return Clock{Now: time.Now, Sleep: time.Sleep} }

func (c Clock) now() time.Time {
	if c.Now == nil {
		return time.Now()
	}
	return c.Now()
}

func (c Clock) sleep(d time.Duration) {
	if c.Sleep == nil {
		time.Sleep(d)
		return
	}
	c.Sleep(d)
}

// waitUntil calls try until it reports true, every interval, for at most
// limit measured from the first call; it reports whether try succeeded. It
// stops early, false, once ctx is done.
func (c Clock) waitUntil(ctx context.Context, limit, every time.Duration, try func() bool) bool {
	deadline := c.now().Add(limit)
	for {
		if try() {
			return true
		}
		if ctx.Err() != nil || !c.now().Before(deadline) {
			return false
		}
		c.sleep(every)
	}
}

// runFor executes one command with its own deadline and returns stdout,
// stderr and the error.
func runFor(ctx context.Context, h host.Host, d time.Duration, name string, args ...string) ([]byte, []byte, error) {
	ctx, cancel := context.WithTimeout(ctx, d)
	defer cancel()
	return h.Run(ctx, name, args...)
}

// docker runs the private engine's CLI against its own socket.
func docker(ctx context.Context, h host.Host, d time.Duration, args ...string) ([]byte, []byte, error) {
	argv := dockerArgs(args...)
	return runFor(ctx, h, d, argv[0], argv[1:]...)
}

// failure is a command's error for a Result detail: its first stderr line when
// it wrote one, else the error itself.
func failure(stderr []byte, err error) string {
	if line, _, _ := strings.Cut(strings.TrimSpace(string(stderr)), "\n"); line != "" {
		return line
	}
	return err.Error()
}

// fileIs reports whether name exists with exactly content.
func fileIs(h host.Host, name, content string) bool {
	b, err := h.ReadFile(name)
	return err == nil && string(b) == content
}

// ----------------------------------------------------------------- Engine

// Engine installs and starts the private Docker engine: the static binaries in
// /opt/povengine/engine and povengine-engine.service (§7.1). An engine that
// already answers with the packaged version under the current unit is left
// alone, so a re-run changes nothing.
type Engine struct {
	Clock
	h       host.Host
	archive string // the static engine .tgz from the package
	version string // the engine version it holds; "" = do not compare
}

func NewEngine(h host.Host, archive, version string) *Engine {
	return &Engine{Clock: realClock(), h: h, archive: archive, version: version}
}

func (*Engine) ID() string    { return "engine" }
func (*Engine) Title() string { return "Container engine" }

// serverVersion asks the private engine for its version; "" when it does not
// answer.
func (s *Engine) serverVersion(ctx context.Context) string {
	out, _, err := docker(ctx, s.h, pollTimeout, "version", "--format", "{{.Server.Version}}")
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// current reports whether the engine runs the packaged version under the
// current unit, and the version that answered ("" = none).
func (s *Engine) current(ctx context.Context) (isCurrent bool, running string) {
	running = s.serverVersion(ctx)
	if running == "" {
		return false, ""
	}
	if s.version != "" && running != s.version {
		return false, running
	}
	if _, err := s.h.Stat(engineDockerd); err != nil {
		return false, running
	}
	return fileIs(s.h, EngineUnitPath, EngineUnit()), running
}

func (s *Engine) Check(ctx context.Context, _ *engine.State) engine.Result {
	current, running := s.current(ctx)
	switch {
	case current:
		return ok("Engine " + running + " is running")
	case running != "" && s.version != "":
		return ok(fmt.Sprintf("Engine %s is running; will replace it with %s", running, s.version))
	case running != "":
		return ok("Engine " + running + " is running; will reinstall and restart it")
	}
	if _, err := s.h.Stat(engineDockerd); err == nil {
		return ok("Engine is installed but not answering; will reinstall and start it")
	}
	return ok("Will install the private engine " + s.version)
}

func (s *Engine) Apply(ctx context.Context, _ *engine.State) engine.Result {
	current, running := s.current(ctx)
	if current {
		return ok("Engine " + running + " is running")
	}
	fail := func(detail string) engine.Result { return block(engine.CodeEngineStartFailed, detail) }
	journal := "; see: journalctl -u " + engineUnitName

	if err := s.h.MkdirAll(engineDir, 0o755); err != nil {
		return fail("cannot create " + engineDir + ": " + err.Error())
	}
	if _, stderr, err := runFor(ctx, s.h, extractTimeout, "tar", "-xzf", s.archive, "-C", engineDir, "--strip-components=1"); err != nil {
		return fail("cannot unpack the engine into " + engineDir + ": " + failure(stderr, err))
	}
	if err := s.h.WriteFile(EngineUnitPath, []byte(EngineUnit()), unitMode); err != nil {
		return fail("cannot write " + EngineUnitPath + ": " + err.Error())
	}
	if _, stderr, err := runFor(ctx, s.h, commandTimeout, "systemctl", "daemon-reload"); err != nil {
		return fail("systemctl daemon-reload failed: " + failure(stderr, err))
	}
	if _, stderr, err := runFor(ctx, s.h, unitTimeout, "systemctl", "enable", "--now", engineUnitName); err != nil {
		return fail("the engine did not start: " + failure(stderr, err) + journal)
	}
	if running != "" {
		// enable --now leaves a running unit alone; the new binaries and
		// unit take effect only on a restart.
		if _, stderr, err := runFor(ctx, s.h, unitTimeout, "systemctl", "restart", engineUnitName); err != nil {
			return fail("the engine did not restart: " + failure(stderr, err) + journal)
		}
	}

	var answered string
	if !s.waitUntil(ctx, engineWait, engineEvery, func() bool {
		answered = s.serverVersion(ctx)
		return answered != ""
	}) {
		return fail(fmt.Sprintf("the engine did not answer on %s within %s", strings.TrimPrefix(ownEngineSocket, "unix://"), engineWait) + journal)
	}
	return ok("Engine " + answered + " is running")
}

// ------------------------------------------------------------------ Image

// Image loads the SimCore image from the package into the private engine and
// refuses unless the engine then reports the package's image id (§7.1): a tag
// alone could name any image.
type Image struct {
	h             host.Host
	ref, id, path string
}

func NewImage(h host.Host, ref, id, path string) *Image {
	return &Image{h: h, ref: ref, id: id, path: path}
}

func (*Image) ID() string    { return "image" }
func (*Image) Title() string { return "SimCore image" }

// loadedID is the id the private engine holds for the image reference; "" when
// it has none (or does not answer).
func (s *Image) loadedID(ctx context.Context) string {
	out, _, err := docker(ctx, s.h, commandTimeout, "image", "inspect", "--format", "{{.Id}}", s.ref)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

// sameImage compares two image ids, with or without the "sha256:" prefix
// Docker prints; the digest itself must match exactly.
func sameImage(a, b string) bool {
	a, b = strings.TrimPrefix(a, "sha256:"), strings.TrimPrefix(b, "sha256:")
	return a != "" && a == b
}

func (s *Image) Check(ctx context.Context, _ *engine.State) engine.Result {
	if sameImage(s.loadedID(ctx), s.id) {
		return ok("Image " + s.ref + " is loaded")
	}
	return ok("Will load image " + s.ref)
}

func (s *Image) Apply(ctx context.Context, st *engine.State) engine.Result {
	if got := s.loadedID(ctx); sameImage(got, s.id) {
		st.Facts[FactImageID] = got
		return ok("Image " + s.ref + " is loaded")
	}
	if _, stderr, err := docker(ctx, s.h, loadTimeout, "load", "-i", s.path); err != nil {
		return block(engine.CodeImageIDMismatch, "could not load the SimCore image "+s.ref+": "+failure(stderr, err))
	}
	got := s.loadedID(ctx)
	if got == "" {
		return block(engine.CodeImageIDMismatch, "image "+s.ref+" is not in the engine after loading "+s.path)
	}
	if !sameImage(got, s.id) {
		return block(engine.CodeImageIDMismatch, fmt.Sprintf("image %s loaded as %s, but the package pins %s", s.ref, got, s.id))
	}
	st.Facts[FactImageID] = got
	return ok("Image " + s.ref + " is loaded")
}
