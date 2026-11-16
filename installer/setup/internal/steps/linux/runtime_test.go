package linux

import (
	"bytes"
	"context"
	"errors"
	"flag"
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/config"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/runtime"
)

var update = flag.Bool("update", false, "rewrite the unit-file golden files from the code")

const (
	dockerCLI     = "/opt/povengine/engine/docker --config /opt/povengine/engine/cli -H unix:///run/povengine/docker.sock"
	versionCmd    = dockerCLI + " version --format {{.Server.Version}}"
	healthCmd     = dockerCLI + " inspect --format {{.State.Health.Status}} povengine-simcore"
	testArchive   = "/tmp/povengine-1.3.0/payload/docker-29.0.4.tgz"
	testEngineVer = "29.0.4"
	tarCmd        = "tar -xzf " + testArchive + " -C /opt/povengine/engine --strip-components=1"
	testImageRef  = "cortex-pov-engine-simcore:1.3.0"
	testImagePath = "/tmp/povengine-1.3.0/payload/simcore-image.tar.gz"
	inspectCmd    = dockerCLI + " image inspect --format {{.Id}} " + testImageRef
	loadCmd       = dockerCLI + " load -i " + testImagePath
	loopbackURL   = "http://127.0.0.1:8888/api/health"
	addressURL    = "http://192.0.2.10:8888/api/health"
)

var (
	idA           = "sha256:" + strings.Repeat("a", 64)
	idB           = "sha256:" + strings.Repeat("b", 64)
	errNotRunning = errors.New("exit status 1")
)

// seqHost answers the commands in seq from a queue, one entry per call; the
// last entry repeats. Every other call goes to the Fake. Calls are recorded in
// the Fake's log either way, so call order stays checkable.
type seqHost struct {
	*host.Fake
	seq map[string][]host.FakeResponse
}

func (s *seqHost) Run(ctx context.Context, name string, args ...string) ([]byte, []byte, error) {
	argv := append([]string{name}, args...)
	key := strings.Join(argv, " ")
	q := s.seq[key]
	if len(q) == 0 {
		return s.Fake.Run(ctx, name, args...)
	}
	s.Fake.Calls = append(s.Fake.Calls, host.Call{Method: "Run", Args: argv})
	if len(q) > 1 {
		s.seq[key] = q[1:]
	}
	return q[0].Stdout, q[0].Stderr, q[0].Err
}

// fakeClock never sleeps: Sleep moves Now forward.
type fakeClock struct {
	t     time.Time
	slept time.Duration
}

func newFakeClock() *fakeClock {
	return &fakeClock{t: time.Date(2026, 11, 16, 12, 0, 0, 0, time.UTC)}
}

func (c *fakeClock) clock() Clock {
	return Clock{
		Now:   func() time.Time { return c.t },
		Sleep: func(d time.Duration) { c.t = c.t.Add(d); c.slept += d },
	}
}

// ops lists the host changes and commands a Fake saw, in order: "run <argv>",
// "write <path> <mode>", "mkdir <path> <mode>", "rm <path>", "get <url>".
// Reads (ReadFile, Stat, LookPath) are left out.
func ops(f *host.Fake) []string {
	var out []string
	for _, c := range f.Calls {
		switch c.Method {
		case "Run":
			out = append(out, "run "+strings.Join(c.Args, " "))
		case "WriteFile":
			out = append(out, "write "+c.Args[0]+" "+c.Mode.String())
		case "MkdirAll":
			out = append(out, "mkdir "+c.Args[0]+" "+c.Mode.String())
		case "RemoveAll":
			out = append(out, "rm "+c.Args[0])
		case "HTTPGet":
			out = append(out, "get "+c.Args[0])
		}
	}
	return out
}

// changes is ops without commands and probes: only what was written.
func changes(f *host.Fake) []string {
	var out []string
	for _, o := range ops(f) {
		if strings.HasPrefix(o, "write ") || strings.HasPrefix(o, "mkdir ") || strings.HasPrefix(o, "rm ") {
			out = append(out, o)
		}
	}
	return out
}

func wantOps(t *testing.T, f *host.Fake, want []string) {
	t.Helper()
	if got := ops(f); !reflect.DeepEqual(got, want) {
		t.Fatalf("host operations:\n got  %q\n want %q", got, want)
	}
}

func runtimeState() *engine.State {
	return &engine.State{
		Version:  "1.3.0",
		Platform: "linux",
		Choices:  engine.Choices{Address: "192.0.2.10", Port: 8888, Firewall: true},
		Facts:    map[string]string{},
	}
}

var ctx = context.Background()

func testPaths() config.Paths { return config.DefaultPaths("linux") }

func goldenRunInput() runtime.RunInput {
	p := testPaths()
	return runtime.RunInput{
		Image:   testImageRef,
		Name:    runtime.ContainerName,
		EnvFile: p.EnvFile,
		DataDir: p.DataDir,
		Address: "192.0.2.10",
		Port:    8888,
	}
}

// ------------------------------------------------------------------ units

func TestUnits_MatchGolden(t *testing.T) {
	for name, got := range map[string]string{
		"povengine-containerd.service.golden": ContainerdUnit(),
		"povengine-engine.service.golden":     EngineUnit(),
		"povengine.service.golden":            ServiceUnit(goldenRunInput()),
	} {
		path := filepath.Join("testdata", name)
		if *update {
			if err := os.WriteFile(path, []byte(got), 0o644); err != nil {
				t.Fatal(err)
			}
			continue
		}
		want, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal([]byte(got), want) {
			t.Errorf("%s differs from the code (rerun with -update only after reading the diff):\n--- got\n%s\n--- want\n%s", name, got, want)
		}
	}
}

// execStart returns a unit's ExecStart= line.
func execStart(t *testing.T, unit string) string {
	t.Helper()
	for _, l := range strings.Split(unit, "\n") {
		if strings.HasPrefix(l, "ExecStart=") {
			return l
		}
	}
	t.Fatalf("no ExecStart= in:\n%s", unit)
	return ""
}

// hasFlags fails for each flag (with its value) missing from line.
func hasFlags(t *testing.T, line string, flags ...string) {
	t.Helper()
	for _, f := range flags {
		if !strings.Contains(line+" ", " "+f+" ") {
			t.Errorf("missing %q in:\n%s", f, line)
		}
	}
}

// The private engine never touches the host firewall (IPv4 or IPv6) and its
// socket is root's alone.
func TestEngineUnit_LeavesTheHostFirewallAndSocketToRoot(t *testing.T) {
	hasFlags(t, execStart(t, EngineUnit()),
		"--iptables=false", "--ip6tables=false", "--ip-masq=false", "--bridge=none", "--group root")
}

// Without --config-file dockerd reads the customer's /etc/docker/daemon.json,
// and without --containerd it attaches to a running system containerd: the
// image would land in the customer's containerd and their restarts or prunes
// would take SimCore down.
func TestEngineUnit_IsolatedFromTheHostsDockerAndContainerd(t *testing.T) {
	unit := EngineUnit()
	hasFlags(t, execStart(t, unit),
		"--config-file /opt/povengine/engine/daemon.json",
		"--containerd /run/povengine/containerd/containerd.sock",
		"--containerd-namespace povengine",
		"--containerd-plugins-namespace povengine-plugins")
	for _, l := range []string{"Requires=povengine-containerd.service\n", "After=povengine-containerd.service"} {
		if !strings.Contains(unit, l) {
			t.Errorf("engine unit lacks %q:\n%s", strings.TrimSpace(l), unit)
		}
	}
	// Stopping the engine must not delete the running containerd's socket
	// and state, which live under the engine's runtime directory.
	if !strings.Contains(unit, "\nRuntimeDirectoryPreserve=yes\n") {
		t.Errorf("engine unit removes /run/povengine (and containerd's state) when it stops:\n%s", unit)
	}

	cd := ContainerdUnit()
	hasFlags(t, execStart(t, cd),
		"--config /opt/povengine/engine/containerd.toml",
		"--address /run/povengine/containerd/containerd.sock",
		"--root /var/lib/povengine/containerd",
		"--state /run/povengine/containerd")
	if containerdConfig != wantContainerdConfig {
		t.Errorf("containerd.toml = %q", containerdConfig)
	}
	if daemonJSON != "{}\n" {
		t.Errorf("daemon.json = %q", daemonJSON)
	}
}

// The docker CLI reads root's ~/.docker/config.json and copies its proxies
// into every container it runs. Every call to the private engine's CLI, the
// installer's and the units', points it at the installer's own empty
// config directory instead.
func TestDockerCLI_NeverReadsRootsConfig(t *testing.T) {
	const prefix = "/opt/povengine/engine/docker --config /opt/povengine/engine/cli -H unix:///run/povengine/docker.sock "
	for _, listenOnly := range []bool{false, true} {
		in := goldenRunInput()
		in.ListenOnly = listenOnly
		for _, l := range strings.Split(ServiceUnit(in), "\n") {
			k, cmd, found := strings.Cut(l, "=")
			if !found || !strings.HasPrefix(k, "Exec") {
				continue
			}
			if !strings.HasPrefix(strings.TrimPrefix(cmd, "-"), prefix) {
				t.Errorf("%s does not use the installer's CLI config:\n%s", k, l)
			}
		}
	}

	// Every docker invocation the steps make, including the port check's.
	h := &seqHost{
		Fake: &host.Fake{
			Paths: map[string]string{"ss": "/usr/bin/ss"},
			Files: map[string][]byte{
				"/opt/povengine/engine/docker": []byte("ELF"),
				"/proc/4242/cgroup":            []byte("0::/system.slice/docker-" + ownID + ".scope\n"),
			},
			Responses: map[string]host.FakeResponse{
				"ss -Hltnp": {Stdout: td(t, "ss-listen-8888-ours.txt")},
				dockerCLI + " inspect --format {{.Id}} povengine-simcore": {Stdout: []byte(ownID + "\n")},
				tarCmd:                    {},
				"systemctl daemon-reload": {},
				enableContainerd:          {},
				enableEngine:              {},
				loadCmd:                   {},
				healthCmd:                 {Stdout: []byte("healthy\n")},
				loopbackURL:               {Status: 200, Body: td(t, "health-api-auth-only.json")},
				addressURL:                {Status: 200, Body: td(t, "health-api-auth-only.json")},
			},
		},
		seq: map[string][]host.FakeResponse{
			// Engine: Check, Apply's own look, then the first poll answers.
			versionCmd: {{Err: errNotRunning}, {Err: errNotRunning}, {Stdout: []byte(testEngineVer + "\n")}},
			// Image: Check, Apply's own look, then after the load.
			inspectCmd: {{Err: errNotRunning}, {Err: errNotRunning}, {Stdout: []byte(idA + "\n")}},
		},
	}
	fc := newFakeClock()
	eng := NewEngine(h, testArchive, testEngineVer)
	eng.Clock = fc.clock()
	hg := NewHealthGate(h)
	hg.Clock = fc.clock()
	st := runtimeState()
	for _, s := range []engine.Step{NewPortFree(h), eng, NewImage(h, testImageRef, idA, testImagePath), hg} {
		if r := s.Check(ctx, st); r.Blocks() {
			t.Fatalf("%s Check: %+v", s.ID(), r)
		}
		if r := s.Apply(ctx, st); r.Blocks() {
			t.Fatalf("%s Apply: %+v", s.ID(), r)
		}
	}
	calls := 0
	for _, c := range h.Calls {
		if c.Method != "Run" || (c.Args[0] != "docker" && !strings.HasSuffix(c.Args[0], "/docker")) {
			continue
		}
		calls++
		if len(c.Args) < 3 || c.Args[1] != "--config" || c.Args[2] != "/opt/povengine/engine/cli" {
			t.Errorf("docker called without the installer's CLI config: %q", c.Args)
		}
	}
	if calls < 5 {
		t.Fatalf("only %d docker calls seen; the scenario did not exercise the steps", calls)
	}
}

// An empty --host must survive systemd's parsing as one empty argument.
func TestServiceUnit_IPv6AddressListensOnEveryAddress(t *testing.T) {
	in := goldenRunInput()
	in.Address = "2001:db8::10"
	line := execStart(t, ServiceUnit(in))
	want := " --pull never " + testImageRef + ` uvicorn main:app --host "" --port 8888 --workers 1 --timeout-graceful-shutdown 5`
	if !strings.HasSuffix(line, want) {
		t.Fatalf("ExecStart does not bind every address:\n%s", line)
	}
	if strings.Contains(line, "--health-cmd") {
		t.Fatalf("the image's own 127.0.0.1 health check answers at 8888; no override wanted:\n%s", line)
	}
	in.Port = 9443
	line = execStart(t, ServiceUnit(in))
	want = ` --health-cmd "curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:9443/api/health" ` + testImageRef +
		` uvicorn main:app --host "" --port 9443 --workers 1 --timeout-graceful-shutdown 5`
	if !strings.HasSuffix(line, want) {
		t.Fatalf("port 9443 ExecStart:\n%s", line)
	}
}

func TestServiceUnit_ListenOnlyQuotesTheHealthCommand(t *testing.T) {
	in := goldenRunInput()
	in.ListenOnly = true
	unit := ServiceUnit(in)
	want := ` --health-cmd "curl -fsS --max-time 5 -o /dev/null http://192.0.2.10:8888/api/health" ` + testImageRef +
		" uvicorn main:app --host 192.0.2.10 --port 8888 --workers 1 --timeout-graceful-shutdown 5\n"
	if !strings.Contains(unit, want) {
		t.Fatalf("ExecStart lacks the quoted health command and uvicorn override:\n%s", unit)
	}
}

func TestSystemdQuote(t *testing.T) {
	for in, want := range map[string]string{
		"plain":          "plain",
		"two words":      `"two words"`,
		"":               `""`,
		`say "hi"`:       `"say \"hi\""`,
		`back\slash`:     `"back\\slash"`,
		"100%":           "100%%",
		"$HOME":          "$$HOME",
		"a $b %c":        `"a $$b %%c"`,
		"tab\tseparated": "\"tab\tseparated\"",
	} {
		if got := systemdQuote(in); got != want {
			t.Errorf("systemdQuote(%q) = %s, want %s", in, got, want)
		}
	}
}

// ----------------------------------------------------------------- engine

// wantContainerdConfig is the private containerd's whole config: no CRI (not a
// Kubernetes node), no /opt/containerd (the opt plugin) and no NRI socket.
const wantContainerdConfig = "version = 2\n" +
	`disabled_plugins = ["io.containerd.grpc.v1.cri", "io.containerd.internal.v1.opt", "io.containerd.nri.v1.nri"]` + "\n"

const (
	enableContainerd = "systemctl enable --now povengine-containerd.service"
	enableEngine     = "systemctl enable --now povengine-engine.service"
	restartEngine    = "systemctl restart povengine-containerd.service povengine-engine.service"
)

// installedEngine is a host where the installer's engine files are all in
// place and current.
func installedEngine() *host.Fake {
	h := &host.Fake{Files: map[string][]byte{
		"/opt/povengine/engine/dockerd":         []byte("ELF"),
		"/opt/povengine/engine/daemon.json":     []byte(daemonJSON),
		"/opt/povengine/engine/containerd.toml": []byte(containerdConfig),
		ContainerdUnitPath:                      []byte(ContainerdUnit()),
		EngineUnitPath:                          []byte(EngineUnit()),
	}}
	_ = h.MkdirAll("/opt/povengine/engine/cli", 0o700)
	h.Calls = nil
	return h
}

func TestEngine_FreshInstall(t *testing.T) {
	fc := newFakeClock()
	h := &seqHost{
		Fake: &host.Fake{Responses: map[string]host.FakeResponse{
			tarCmd:                    {},
			"systemctl daemon-reload": {},
			enableContainerd:          {},
			enableEngine:              {},
		}},
		seq: map[string][]host.FakeResponse{
			// Check, Apply's own look, then two polls: not yet, then up.
			versionCmd: {{Err: errNotRunning}, {Err: errNotRunning}, {Err: errNotRunning}, {Stdout: []byte(testEngineVer + "\n")}},
		},
	}
	s := NewEngine(h, testArchive, testEngineVer)
	s.Clock = fc.clock()
	st := runtimeState()

	if r := s.Check(ctx, st); r.Code != engine.CodeOK || r.Blocks() {
		t.Fatalf("Check on a fresh host: %+v", r)
	}
	if got := changes(h.Fake); len(got) != 0 {
		t.Fatalf("Check changed the host: %q", got)
	}
	if r := s.Apply(ctx, st); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	wantOps(t, h.Fake, []string{
		"run " + versionCmd,
		"run " + versionCmd,
		"mkdir /opt/povengine/engine -rwxr-xr-x",
		"run " + tarCmd,
		"mkdir /opt/povengine/engine/cli -rwx------",
		"write /opt/povengine/engine/daemon.json -rw-r--r--",
		"write /opt/povengine/engine/containerd.toml -rw-r--r--",
		"write /etc/systemd/system/povengine-containerd.service -rw-r--r--",
		"write /etc/systemd/system/povengine-engine.service -rw-r--r--",
		"run systemctl daemon-reload",
		"run " + enableContainerd,
		"run " + enableEngine,
		"run " + versionCmd,
		"run " + versionCmd,
	})
	for path, want := range map[string]string{
		EngineUnitPath:                          EngineUnit(),
		ContainerdUnitPath:                      ContainerdUnit(),
		"/opt/povengine/engine/daemon.json":     "{}\n",
		"/opt/povengine/engine/containerd.toml": wantContainerdConfig,
	} {
		if got := string(h.Files[path]); got != want {
			t.Errorf("%s:\n%s\nwant:\n%s", path, got, want)
		}
	}
	if fc.slept != engineEvery {
		t.Fatalf("slept %v, want one poll interval (%v)", fc.slept, engineEvery)
	}
}

func TestEngine_NeverAnswersIsStartFailed(t *testing.T) {
	fc := newFakeClock()
	h := &host.Fake{Responses: map[string]host.FakeResponse{
		tarCmd:                    {},
		"systemctl daemon-reload": {},
		enableContainerd:          {},
		enableEngine:              {},
		versionCmd:                {Err: errNotRunning},
	}}
	s := NewEngine(h, testArchive, testEngineVer)
	s.Clock = fc.clock()
	start := time.Now()
	r := s.Apply(ctx, runtimeState())
	if r.Code != engine.CodeEngineStartFailed || !r.Blocks() {
		t.Fatalf("got %+v, want ENGINE_START_FAILED", r)
	}
	if fc.t.Sub(newFakeClock().t) < 30*time.Second {
		t.Fatalf("gave up after %v of clock time, want at least 30 s", fc.t.Sub(newFakeClock().t))
	}
	if real := time.Since(start); real > 5*time.Second {
		t.Fatalf("the test slept for real (%v)", real)
	}
	if !strings.Contains(r.Detail, "journalctl -u povengine-engine.service") {
		t.Errorf("detail should say where to look: %q", r.Detail)
	}
}

func TestEngine_EnableFailureIsStartFailed(t *testing.T) {
	for unit, key := range map[string]string{
		"povengine-containerd.service": enableContainerd,
		"povengine-engine.service":     enableEngine,
	} {
		h := &host.Fake{Responses: map[string]host.FakeResponse{
			tarCmd:                    {},
			"systemctl daemon-reload": {},
			enableContainerd:          {},
			enableEngine:              {},
			versionCmd:                {Err: errNotRunning},
		}}
		h.Responses[key] = host.FakeResponse{Stderr: []byte("Job for " + unit + " failed.\n"), Err: errNotRunning}
		s := NewEngine(h, testArchive, testEngineVer)
		s.Clock = newFakeClock().clock()
		r := s.Apply(ctx, runtimeState())
		if r.Code != engine.CodeEngineStartFailed {
			t.Fatalf("%s: got %+v, want ENGINE_START_FAILED", unit, r)
		}
		if !strings.Contains(r.Detail, "Job for "+unit+" failed.") || !strings.Contains(r.Detail, "journalctl -u "+unit) {
			t.Errorf("%s: detail should carry systemctl's message and the unit's journal: %q", unit, r.Detail)
		}
	}
}

func TestEngine_AlreadyRunningIsIdempotent(t *testing.T) {
	h := installedEngine()
	h.Responses = map[string]host.FakeResponse{versionCmd: {Stdout: []byte(testEngineVer + "\n")}}
	s := NewEngine(h, testArchive, testEngineVer)
	s.Clock = newFakeClock().clock()
	st := runtimeState()
	if r := s.Check(ctx, st); r.Code != engine.CodeOK || !strings.Contains(r.Detail, "running") {
		t.Fatalf("Check: %+v", r)
	}
	if r := s.Apply(ctx, st); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	wantOps(t, h, []string{"run " + versionCmd, "run " + versionCmd})
}

func TestEngine_RunningOtherVersionIsReplacedAndRestarted(t *testing.T) {
	h := installedEngine()
	h.Responses = map[string]host.FakeResponse{
		versionCmd:                {Stdout: []byte("28.5.2\n")},
		tarCmd:                    {},
		"systemctl daemon-reload": {},
		enableContainerd:          {},
		enableEngine:              {},
		restartEngine:             {},
	}
	s := NewEngine(h, testArchive, testEngineVer)
	s.Clock = newFakeClock().clock()
	if r := s.Apply(ctx, runtimeState()); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	got := strings.Join(ops(h), "\n")
	for _, want := range []string{"run " + tarCmd, "run " + restartEngine} {
		if !strings.Contains(got, want) {
			t.Fatalf("a running engine of another version must be replaced and restarted; missing %q in:\n%s", want, got)
		}
	}
}

// A daemon.json someone edited (log driver, storage driver, userns remap...)
// would be inherited silently; the installer puts its own back and restarts.
func TestEngine_EditedDaemonJSONIsRewritten(t *testing.T) {
	h := installedEngine()
	h.Files["/opt/povengine/engine/daemon.json"] = []byte(`{"log-driver":"syslog"}`)
	h.Responses = map[string]host.FakeResponse{
		versionCmd:                {Stdout: []byte(testEngineVer + "\n")},
		tarCmd:                    {},
		"systemctl daemon-reload": {},
		enableContainerd:          {},
		enableEngine:              {},
		restartEngine:             {},
	}
	s := NewEngine(h, testArchive, testEngineVer)
	s.Clock = newFakeClock().clock()
	if r := s.Check(ctx, runtimeState()); r.Code != engine.CodeOK || strings.HasSuffix(r.Detail, "is running") {
		t.Fatalf("Check took an edited daemon.json for current: %+v", r)
	}
	if r := s.Apply(ctx, runtimeState()); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	if got := string(h.Files["/opt/povengine/engine/daemon.json"]); got != "{}\n" {
		t.Fatalf("daemon.json = %q, want {}", got)
	}
	if !strings.Contains(strings.Join(ops(h), "\n"), "run "+restartEngine) {
		t.Fatalf("the engine was not restarted onto the rewritten config: %q", ops(h))
	}
}

// ------------------------------------------------------------------ image

func TestImage_IdMismatchRefuses(t *testing.T) {
	h := &host.Fake{Responses: map[string]host.FakeResponse{
		inspectCmd: {Stdout: []byte(idB + "\n")},
		loadCmd:    {Stdout: []byte("Loaded image: " + testImageRef + "\n")},
	}}
	st := runtimeState()
	r := NewImage(h, testImageRef, idA, testImagePath).Apply(ctx, st)
	if r.Code != engine.CodeImageIDMismatch || !r.Blocks() {
		t.Fatalf("got %+v, want IMAGE_ID_MISMATCH", r)
	}
	if !strings.Contains(strings.Join(ops(h), "\n"), "run "+loadCmd) {
		t.Fatalf("the image was not loaded before the id was judged: %q", ops(h))
	}
	if !strings.Contains(r.Detail, idA) || !strings.Contains(r.Detail, idB) {
		t.Errorf("detail should name both ids: %q", r.Detail)
	}
	if _, set := st.Facts[FactImageID]; set {
		t.Errorf("a refused image must not be published as the install's image id")
	}
}

func TestImage_LoadsWhenAbsent(t *testing.T) {
	h := &seqHost{
		Fake: &host.Fake{Responses: map[string]host.FakeResponse{loadCmd: {}}},
		seq: map[string][]host.FakeResponse{
			inspectCmd: {{Err: errNotRunning}, {Err: errNotRunning}, {Stdout: []byte(idA + "\n")}},
		},
	}
	s := NewImage(h, testImageRef, idA, testImagePath)
	st := runtimeState()
	if r := s.Check(ctx, st); r.Code != engine.CodeOK || r.Blocks() {
		t.Fatalf("Check: %+v", r)
	}
	if r := s.Apply(ctx, st); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	wantOps(t, h.Fake, []string{"run " + inspectCmd, "run " + inspectCmd, "run " + loadCmd, "run " + inspectCmd})
	if st.Facts[FactImageID] != idA {
		t.Errorf("fact %s = %q, want %q", FactImageID, st.Facts[FactImageID], idA)
	}
}

func TestImage_AlreadyLoadedSkipsTheLoad(t *testing.T) {
	// The package may pin the id with or without the "sha256:" prefix.
	h := &host.Fake{Responses: map[string]host.FakeResponse{inspectCmd: {Stdout: []byte(idA + "\n")}}}
	r := NewImage(h, testImageRef, strings.TrimPrefix(idA, "sha256:"), testImagePath).Apply(ctx, runtimeState())
	if r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
	wantOps(t, h, []string{"run " + inspectCmd})
}

func TestImage_LoadFailureRefuses(t *testing.T) {
	h := &host.Fake{Responses: map[string]host.FakeResponse{
		inspectCmd: {Err: errNotRunning},
		loadCmd:    {Stderr: []byte("write /var/lib/povengine/engine/tmp: no space left on device\n"), Err: errNotRunning},
	}}
	r := NewImage(h, testImageRef, idA, testImagePath).Apply(ctx, runtimeState())
	if r.Code != engine.CodeImageIDMismatch || !strings.Contains(r.Detail, "no space left on device") {
		t.Fatalf("got %+v, want IMAGE_ID_MISMATCH carrying docker's message", r)
	}
}

// ----------------------------------------------------------------- config

const testSecret = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4"

func configHost() *host.Fake {
	return &host.Fake{Responses: map[string]host.FakeResponse{
		"hostname -s": {Stdout: []byte("povjump\n")},
		"hostname -f": {Stdout: []byte("povjump.example.test\n")},
	}}
}

// noSecretAnywhere fails when the secret appears in the result or in a fact.
func noSecretAnywhere(t *testing.T, secret string, r engine.Result, st *engine.State) {
	t.Helper()
	if secret == "" {
		t.Fatal("empty secret")
	}
	if strings.Contains(r.Detail, secret) {
		t.Fatalf("the secret is in the result detail: %q", r.Detail)
	}
	for k, v := range st.Facts {
		if strings.Contains(v, secret) {
			t.Fatalf("the secret is in fact %s", k)
		}
	}
}

func TestConfig_WritesEnvFile0600(t *testing.T) {
	h := configHost()
	p := testPaths()
	st := runtimeState()
	st.Facts[FactAddressCandidates] = "192.0.2.10,198.51.100.7"
	st.Facts[FactProxy] = "http://proxy.example.test:3128"

	s := NewConfig(h, p)
	if r := s.Check(ctx, st); r.Code != engine.CodeOK || r.Blocks() || !strings.Contains(r.Detail, "Will create") {
		t.Fatalf("Check on a fresh host: %+v", r)
	}
	if got := changes(h); len(got) != 0 {
		t.Fatalf("Check changed the host: %q", got)
	}
	r := s.Apply(ctx, st)
	if r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	secret := strings.TrimSpace(string(h.Files[p.SecretFile]))
	noSecretAnywhere(t, secret, r, st)

	var envMode fs.FileMode
	for _, c := range h.Calls {
		if c.Method == "WriteFile" && c.Args[0] == p.EnvFile {
			envMode = c.Mode
		}
	}
	if envMode != 0o600 {
		t.Fatalf("env file written with mode %v, want 0600", envMode)
	}
	want, err := config.RenderEnv(config.EnvInput{
		Secret: secret, Version: "1.3.0", Platform: "linux", Address: "192.0.2.10", Port: 8888,
		ShortName: "povjump", FQDN: "povjump.example.test", Proxy: "http://proxy.example.test:3128",
		LocalAddrs: []string{"192.0.2.10", "198.51.100.7"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if got := h.Files[p.EnvFile]; !bytes.Equal(got, want) {
		t.Fatalf("env file:\n%s\nwant:\n%s", got, want)
	}
	if !bytes.Contains(h.Files[p.EnvFile], []byte("\nCORTEXSIM_PORT=8888\n")) {
		t.Fatal("the env file does not carry the chosen port")
	}
}

func TestConfig_KeepsAnExistingSecret(t *testing.T) {
	h := configHost()
	p := testPaths()
	h.Files = map[string][]byte{p.SecretFile: []byte(testSecret + "\n")}
	st := runtimeState()
	r := NewConfig(h, p).Apply(ctx, st)
	if r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	noSecretAnywhere(t, testSecret, r, st)
	if !strings.Contains(string(h.Files[p.EnvFile]), "CORTEXSIM_SECRET="+testSecret+"\n") {
		t.Fatal("the env file does not carry the existing secret")
	}
	for _, c := range h.Calls {
		if c.Method == "WriteFile" && c.Args[0] == p.SecretFile {
			t.Fatal("an existing secret was rewritten")
		}
	}
}

func TestConfig_CheckReportsPresenceOnly(t *testing.T) {
	h := configHost()
	p := testPaths()
	h.Files = map[string][]byte{
		p.SecretFile: []byte(testSecret + "\n"),
		p.EnvFile:    []byte("CORTEXSIM_SECRET=" + testSecret + "\n"),
	}
	st := runtimeState()
	r := NewConfig(h, p).Check(ctx, st)
	if r.Code != engine.CodeOK || r.Blocks() {
		t.Fatalf("Check: %+v", r)
	}
	noSecretAnywhere(t, testSecret, r, st)
	for _, c := range h.Calls {
		if c.Method == "ReadFile" {
			t.Fatalf("Check read %s; it may only report whether the files exist", c.Args[0])
		}
	}
	if got := changes(h); len(got) != 0 {
		t.Fatalf("Check changed the host: %q", got)
	}
}

func TestConfig_CredentialedProxyBlocksWithItsCode(t *testing.T) {
	h := configHost()
	st := runtimeState()
	st.Facts[FactProxy] = "http://alice:hunter2@proxy.example.test:3128"
	r := NewConfig(h, testPaths()).Apply(ctx, st)
	if r.Code != engine.CodeProxyAuthUnsupported || !r.Blocks() {
		t.Fatalf("got %+v, want PROXY_AUTH_UNSUPPORTED", r)
	}
	if strings.Contains(r.Detail, "hunter2") {
		t.Fatalf("the proxy password is in the detail: %q", r.Detail)
	}
	if _, written := h.Files[testPaths().EnvFile]; written {
		t.Fatal("an env file was written after a refusal")
	}
}

func TestConfig_InvalidExistingSecretBlocksWithoutEchoingIt(t *testing.T) {
	h := configHost()
	p := testPaths()
	const bad = "not-a-valid-secret-value"
	h.Files = map[string][]byte{p.SecretFile: []byte(bad + "\n")}
	r := NewConfig(h, p).Apply(ctx, runtimeState())
	if r.Code != engine.CodeConfigWriteFailed || !r.Blocks() {
		t.Fatalf("got %+v, want CONFIG_WRITE_FAILED", r)
	}
	if strings.Contains(r.Detail, bad) {
		t.Fatalf("the secret file's content is in the detail: %q", r.Detail)
	}
	if string(h.Files[p.SecretFile]) != bad+"\n" {
		t.Fatal("an invalid secret file was replaced")
	}
}

// ---------------------------------------------------------------- service

func TestService_InstallsTheUnitAndStartsIt(t *testing.T) {
	h := &host.Fake{Responses: map[string]host.FakeResponse{
		"systemctl daemon-reload":             {},
		"systemctl enable povengine.service":  {},
		"systemctl restart povengine.service": {},
	}}
	p := testPaths()
	s := NewService(h, p, testImageRef)
	st := runtimeState()
	if r := s.Check(ctx, st); r.Code != engine.CodeOK || r.Blocks() {
		t.Fatalf("Check on a fresh host: %+v", r)
	}
	h.Calls = nil
	if r := s.Apply(ctx, st); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	wantOps(t, h, []string{
		"mkdir /var/lib/povengine/data -rwx------",
		"write /etc/systemd/system/povengine.service -rw-r--r--",
		"run systemctl daemon-reload",
		"run systemctl enable povengine.service",
		"run systemctl restart povengine.service",
	})
	if got := string(h.Files[ServiceUnitPath]); got != ServiceUnit(goldenRunInput()) {
		t.Fatalf("unit file is not ServiceUnit(state):\n%s", got)
	}
}

func TestService_StartFailureIsUnhealthy(t *testing.T) {
	h := &host.Fake{Responses: map[string]host.FakeResponse{
		"systemctl daemon-reload":             {},
		"systemctl enable povengine.service":  {},
		"systemctl restart povengine.service": {Stderr: []byte("Job for povengine.service failed.\n"), Err: errNotRunning},
	}}
	r := NewService(h, testPaths(), testImageRef).Apply(ctx, runtimeState())
	if r.Code != engine.CodeSimCoreUnhealthy || !strings.Contains(r.Detail, "journalctl -u povengine.service") {
		t.Fatalf("got %+v, want SIMCORE_UNHEALTHY pointing at the journal", r)
	}
}

func TestService_CheckSeesARunningInstall(t *testing.T) {
	h := &host.Fake{
		Files:     map[string][]byte{ServiceUnitPath: []byte(ServiceUnit(goldenRunInput()))},
		Responses: map[string]host.FakeResponse{"systemctl is-active povengine.service": {Stdout: []byte("active\n")}},
	}
	r := NewService(h, testPaths(), testImageRef).Check(ctx, runtimeState())
	if r.Code != engine.CodeOK || !strings.Contains(r.Detail, "running") {
		t.Fatalf("got %+v", r)
	}
}

// --------------------------------------------------------------- firewall

func TestFirewall_UfwActive(t *testing.T) {
	h := &host.Fake{
		Paths: map[string]string{"ufw": "/usr/sbin/ufw", "firewall-cmd": "/usr/bin/firewall-cmd"},
		Responses: map[string]host.FakeResponse{
			"env LC_ALL=C ufw status": {Stdout: []byte("Status: active\n\nTo                         Action      From\n")},
			"ufw allow 8888/tcp":      {Stdout: []byte("Rule added\n")},
		},
	}
	st := runtimeState()
	s := NewFirewall(h)
	if r := s.Check(ctx, st); r.Code != engine.CodeOK || st.Facts[FactFirewall] != "ufw" {
		t.Fatalf("Check: %+v facts=%v", r, st.Facts)
	}
	if got := changes(h); len(got) != 0 {
		t.Fatalf("Check changed the host: %q", got)
	}
	h.Calls = nil
	if r := s.Apply(ctx, st); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	wantOps(t, h, []string{"run env LC_ALL=C ufw status", "run ufw allow 8888/tcp"})
	if st.Facts[FactFirewall] != "ufw" {
		t.Fatalf("fact = %q", st.Facts[FactFirewall])
	}
}

func TestFirewall_FirewalldRunning(t *testing.T) {
	h := &host.Fake{
		Paths: map[string]string{"ufw": "/usr/sbin/ufw", "firewall-cmd": "/usr/bin/firewall-cmd"},
		Responses: map[string]host.FakeResponse{
			"env LC_ALL=C ufw status":                      {Stdout: []byte("Status: inactive\n")},
			"firewall-cmd --state":                         {Stdout: []byte("running\n")},
			"firewall-cmd --permanent --add-port=9443/tcp": {Stdout: []byte("success\n")},
			"firewall-cmd --reload":                        {Stdout: []byte("success\n")},
		},
	}
	st := runtimeState()
	st.Choices.Port = 9443
	if r := NewFirewall(h).Apply(ctx, st); r.Code != engine.CodeOK {
		t.Fatalf("Apply: %+v", r)
	}
	wantOps(t, h, []string{
		"run env LC_ALL=C ufw status",
		"run firewall-cmd --state",
		"run firewall-cmd --permanent --add-port=9443/tcp",
		"run firewall-cmd --reload",
	})
	if st.Facts[FactFirewall] != "firewalld" {
		t.Fatalf("fact = %q", st.Facts[FactFirewall])
	}
}

func TestFirewall_NoneActive(t *testing.T) {
	h := &host.Fake{}
	st := runtimeState()
	r := NewFirewall(h).Apply(ctx, st)
	if r.Code != engine.CodeOK || r.Detail != "no host firewall active" {
		t.Fatalf("got %+v", r)
	}
	if st.Facts[FactFirewall] != "none" {
		t.Fatalf("fact = %q", st.Facts[FactFirewall])
	}
	if got := ops(h); len(got) != 0 {
		t.Fatalf("ran %q with no firewall tool present", got)
	}
}

func TestFirewall_RuleFailureBlocks(t *testing.T) {
	h := &host.Fake{
		Paths: map[string]string{"firewall-cmd": "/usr/bin/firewall-cmd"},
		Responses: map[string]host.FakeResponse{
			"firewall-cmd --state":                         {Stdout: []byte("running\n")},
			"firewall-cmd --permanent --add-port=8888/tcp": {Stderr: []byte("Error: INVALID_ZONE\n"), Err: errNotRunning},
		},
	}
	r := NewFirewall(h).Apply(ctx, runtimeState())
	if r.Code != engine.CodeFirewallRuleFailed || !r.Blocks() || !strings.Contains(r.Detail, "INVALID_ZONE") {
		t.Fatalf("got %+v, want FIREWALL_RULE_FAILED carrying the tool's message", r)
	}
}

func TestFirewall_SkippedWhenNotChosen(t *testing.T) {
	h := &host.Fake{Paths: map[string]string{"ufw": "/usr/sbin/ufw"}}
	st := runtimeState()
	st.Choices.Firewall = false
	s := NewFirewall(h)
	for _, r := range []engine.Result{s.Check(ctx, st), s.Apply(ctx, st)} {
		if r.Code != engine.CodeOK || r.Detail != "skipped" {
			t.Fatalf("got %+v, want OK skipped", r)
		}
	}
	if len(h.Calls) != 0 {
		t.Fatalf("a skipped firewall step touched the host: %+v", h.Calls)
	}
}

// ------------------------------------------------------------ health gate

func healthHost(t *testing.T, body []byte) *seqHost {
	t.Helper()
	return &seqHost{
		Fake: &host.Fake{Responses: map[string]host.FakeResponse{
			healthCmd:   {Stdout: []byte("healthy\n")},
			loopbackURL: {Status: 200, Body: body},
			addressURL:  {Status: 200, Body: body},
		}},
		seq: map[string][]host.FakeResponse{},
	}
}

func gate(h host.Host, fc *fakeClock) *HealthGate {
	g := NewHealthGate(h)
	g.Clock = fc.clock()
	return g
}

func TestHealthGate_ApiAuthOnlyIsOK(t *testing.T) {
	h := healthHost(t, td(t, "health-api-auth-only.json"))
	st := runtimeState()
	r := gate(h, newFakeClock()).Apply(ctx, st)
	if r.Code != engine.CodeOK || r.Warning {
		t.Fatalf("got %+v, want plain OK", r)
	}
	if _, set := st.Facts[FactHealthFaults]; set {
		t.Fatalf("faults fact set on a healthy install: %q", st.Facts[FactHealthFaults])
	}
	wantOps(t, h.Fake, []string{"run " + healthCmd, "get " + loopbackURL, "get " + addressURL})
}

func TestHealthGate_FaultsCompleteWithAWarning(t *testing.T) {
	h := healthHost(t, td(t, "health-payload-shelf.json"))
	st := runtimeState()
	r := gate(h, newFakeClock()).Apply(ctx, st)
	if r.Code != engine.CodeOK || !r.Warning || r.Blocks() {
		t.Fatalf("got %+v, want OK with Warning", r)
	}
	if r.Detail != "faults: payload_shelf" {
		t.Fatalf("detail = %q", r.Detail)
	}
	if st.Facts[FactHealthFaults] != "payload_shelf" {
		t.Fatalf("fact %s = %q", FactHealthFaults, st.Facts[FactHealthFaults])
	}
}

func TestHealthGate_MasterKeyMismatchBlocks(t *testing.T) {
	h := healthHost(t, td(t, "health-master-key-mismatch.json"))
	r := gate(h, newFakeClock()).Apply(ctx, runtimeState())
	if r.Code != engine.CodeMasterKeyMismatch || !r.Blocks() {
		t.Fatalf("got %+v, want MASTER_KEY_MISMATCH", r)
	}
}

func TestHealthGate_UnparseableHealthBlocks(t *testing.T) {
	h := healthHost(t, []byte(`{"status":"ok"}`))
	r := gate(h, newFakeClock()).Apply(ctx, runtimeState())
	if r.Code != engine.CodeSimCoreUnhealthy || !r.Blocks() {
		t.Fatalf("got %+v, want SIMCORE_UNHEALTHY", r)
	}
}

func TestHealthGate_AddressUnreachable(t *testing.T) {
	h := healthHost(t, td(t, "health-api-auth-only.json"))
	h.Responses[addressURL] = host.FakeResponse{Err: errors.New("dial tcp 192.0.2.10:8888: connect: connection refused")}
	r := gate(h, newFakeClock()).Apply(ctx, runtimeState())
	if r.Code != engine.CodeAddressUnreachable || !r.Blocks() {
		t.Fatalf("got %+v, want ADDRESS_UNREACHABLE", r)
	}
	if !strings.Contains(r.Detail, "192.0.2.10:8888") {
		t.Errorf("detail should name the address: %q", r.Detail)
	}
}

func TestHealthGate_WaitsForTheContainerToBeHealthy(t *testing.T) {
	h := healthHost(t, td(t, "health-api-auth-only.json"))
	h.seq[healthCmd] = []host.FakeResponse{
		{Stdout: []byte("starting\n")}, {Stdout: []byte("starting\n")}, {Stdout: []byte("healthy\n")},
	}
	fc := newFakeClock()
	if r := gate(h, fc).Apply(ctx, runtimeState()); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
	if fc.slept != 2*healthEvery {
		t.Fatalf("slept %v, want two poll intervals", fc.slept)
	}
}

func TestHealthGate_NeverHealthyIsUnhealthy(t *testing.T) {
	h := healthHost(t, td(t, "health-api-auth-only.json"))
	h.Responses[healthCmd] = host.FakeResponse{Stdout: []byte("unhealthy\n")}
	fc := newFakeClock()
	start := time.Now()
	r := gate(h, fc).Apply(ctx, runtimeState())
	if r.Code != engine.CodeSimCoreUnhealthy || !r.Blocks() {
		t.Fatalf("got %+v, want SIMCORE_UNHEALTHY", r)
	}
	if waited := fc.t.Sub(newFakeClock().t); waited < 120*time.Second {
		t.Fatalf("gave up after %v of clock time, want at least 120 s", waited)
	}
	if real := time.Since(start); real > 5*time.Second {
		t.Fatalf("the test slept for real (%v)", real)
	}
	for _, o := range ops(h.Fake) {
		if strings.HasPrefix(o, "get ") {
			t.Fatalf("probed %s although the container never became healthy", o)
		}
	}
	if !strings.Contains(r.Detail, "unhealthy") {
		t.Errorf("detail should name the last state seen: %q", r.Detail)
	}
}

func TestHealthGate_ListenOnlyProbesTheAddress(t *testing.T) {
	h := healthHost(t, td(t, "health-api-auth-only.json"))
	delete(h.Responses, loopbackURL) // uvicorn is not listening on loopback
	st := runtimeState()
	st.Choices.ListenOnly = true
	if r := gate(h, newFakeClock()).Apply(ctx, st); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
	wantOps(t, h.Fake, []string{"run " + healthCmd, "get " + addressURL})
}

// ------------------------------------------------------------------- plan

func TestInstallPlan_Order(t *testing.T) {
	steps := InstallPlan(&host.Fake{}, testPaths(), PlanInput{
		ImageRef: testImageRef, ImageID: idA, ImagePath: testImagePath,
		EngineArchive: testArchive, EngineVersion: testEngineVer,
	})
	var ids []string
	for _, s := range steps {
		ids = append(ids, s.ID())
	}
	want := []string{"host", "ports", "compose", "address", "proxy", "protection",
		"engine", "config", "image", "service", "firewall", "health"}
	if !reflect.DeepEqual(ids, want) {
		t.Fatalf("plan order:\n got  %q\n want %q", ids, want)
	}
}

// In apply mode a blocking Check stops the run before its Apply, so on a host
// with nothing installed yet no runtime step's Check may block, and none may
// change the host: Check is the dry run.
func TestRuntimeChecks_NeverBlockOrWriteOnAFreshHost(t *testing.T) {
	h := &host.Fake{}
	in := PlanInput{ImageRef: testImageRef, ImageID: idA, ImagePath: testImagePath,
		EngineArchive: testArchive, EngineVersion: testEngineVer}
	for _, s := range InstallPlan(h, testPaths(), in)[6:] {
		st := runtimeState()
		if r := s.Check(ctx, st); r.Blocks() {
			t.Errorf("%s: Check blocked on a fresh host: %+v", s.ID(), r)
		}
	}
	if got := changes(h); len(got) != 0 {
		t.Fatalf("Checks changed the host: %q", got)
	}
}
