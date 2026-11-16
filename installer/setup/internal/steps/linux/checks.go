package linux

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

const (
	minDiskKiB = 10 << 20 // 10 GiB, in 1024-byte blocks (df -Pk)
	// 3.5 GiB of MemTotal (kB): a nominal 4 GB machine reports 3.7-3.9 GiB.
	minMemoryKiB = 3670016

	defaultPort = 8888

	ownEngineSocket    = "unix:///run/povengine/docker.sock"
	ownEngineDocker    = "/opt/povengine/engine/docker"
	ownContainerName   = "povengine-simcore"
	composeProject     = "cortex-pov-engine"
	composeNamePrefix  = "cortex-pov-engine-simcore-"
	commandTimeout     = 10 * time.Second
	composeStopCommand = "docker compose -p " + composeProject + " stop"
)

// checkOnly is embedded by every step: Apply reports OK and touches nothing.
// Task 8 owns host changes.
type checkOnly struct{}

func (checkOnly) Apply(context.Context, *engine.State) engine.Result {
	return engine.Result{Code: engine.CodeOK}
}

func ok(detail string) engine.Result { return engine.Result{Code: engine.CodeOK, Detail: detail} }

func block(code engine.Code, detail string) engine.Result {
	return engine.Result{Code: code, Detail: detail}
}

func warn(code engine.Code, detail string) engine.Result {
	return engine.Result{Code: code, Detail: detail, Warning: true}
}

// run executes one command with its own deadline and returns stdout.
func run(ctx context.Context, h host.Host, name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, commandTimeout)
	defer cancel()
	out, _, err := h.Run(ctx, name, args...)
	return out, err
}

// ---------------------------------------------------------------- HostCheck

// HostCheck judges the machine itself: OS, architecture, systemd, cgroup v2,
// disk and memory (§7.1). The first blocking property is reported; a RHEL-family
// best-effort warning is reported only when nothing blocks.
type HostCheck struct {
	checkOnly
	h host.Host
}

func NewHostCheck(h host.Host) *HostCheck { return &HostCheck{h: h} }

func (*HostCheck) ID() string    { return "host" }
func (*HostCheck) Title() string { return "Host" }

func (s *HostCheck) Check(ctx context.Context, st *engine.State) engine.Result {
	var warning *engine.Result

	osr, err := s.h.ReadFile("/etc/os-release")
	if err != nil {
		return block(engine.CodeOSUnsupported, "cannot read /etc/os-release; supported: Ubuntu 22.04 and 24.04")
	}
	rel := parseOSRelease(osr)
	pretty := rel["PRETTY_NAME"]
	if pretty == "" {
		pretty = strings.TrimSpace(rel["NAME"] + " " + rel["VERSION_ID"])
	}
	switch kind, label := classifyOS(rel); kind {
	case osSupported:
	case osBestEffort:
		w := warn(engine.CodeOSUnsupported, fmt.Sprintf("%s: RHEL family 9 is best effort until a run proves it", label))
		warning = &w
	default:
		return block(engine.CodeOSUnsupported, fmt.Sprintf("%s is not supported; use Ubuntu 22.04 or 24.04", orUnknown(pretty)))
	}
	st.Facts[FactOS] = pretty

	arch, ok2 := s.arch(ctx)
	if !ok2 {
		return block(engine.CodeArchUnsupported, fmt.Sprintf("architecture %q is not supported; use x86_64 or aarch64", arch))
	}
	st.Facts[FactArch] = arch

	comm, err := s.h.ReadFile("/proc/1/comm")
	if err != nil || strings.TrimSpace(string(comm)) != "systemd" {
		return block(engine.CodeNoSystemd, "systemd is not running as PID 1; the installer needs it")
	}

	mi, err := s.h.ReadFile("/proc/self/mountinfo")
	if err != nil || !hasCgroup2(mi) {
		return block(engine.CodeNoCgroupV2, "cgroup v2 is not mounted; boot with the unified cgroup hierarchy")
	}

	if cpus := s.cpus(); cpus > 0 {
		st.Facts[FactCPUs] = strconv.Itoa(cpus)
	}

	freeKiB, err := s.diskFreeKiB(ctx)
	if err != nil {
		return block(engine.CodeDiskLow, "cannot read free space under /var/lib: "+err.Error())
	}
	st.Facts[FactDiskFreeGiB] = strconv.FormatInt(freeKiB>>20, 10)
	if freeKiB < minDiskKiB {
		return block(engine.CodeDiskLow, fmt.Sprintf("%d GiB free under /var/lib; at least 10 GiB needed", freeKiB>>20))
	}

	memKiB, err := s.memoryKiB()
	if err != nil {
		return block(engine.CodeMemoryLow, "cannot read total memory: "+err.Error())
	}
	st.Facts[FactMemoryGiB] = strconv.FormatInt(memKiB>>20, 10)
	if memKiB < minMemoryKiB {
		return block(engine.CodeMemoryLow, fmt.Sprintf("%.1f GiB of memory; a 4 GB-class machine (at least 3.5 GiB usable) is needed", float64(memKiB)/(1<<20)))
	}

	if warning != nil {
		return *warning
	}
	return ok(fmt.Sprintf("%s · %s · %s GiB free", pretty, arch, st.Facts[FactDiskFreeGiB]))
}

func orUnknown(s string) string {
	if s == "" {
		return "this OS"
	}
	return s
}

const (
	osUnsupported = iota
	osSupported
	osBestEffort
)

func classifyOS(rel map[string]string) (kind int, label string) {
	id := strings.ToLower(rel["ID"])
	ver := rel["VERSION_ID"]
	if id == "ubuntu" {
		if ver == "22.04" || ver == "24.04" {
			return osSupported, ""
		}
		return osUnsupported, ""
	}
	like := strings.Fields(strings.ToLower(rel["ID_LIKE"]))
	rhel := id == "rhel" || id == "rocky" || id == "almalinux" || id == "centos"
	for _, l := range like {
		if l == "rhel" {
			rhel = true
		}
	}
	if rhel {
		major, _, _ := strings.Cut(ver, ".")
		if major == "9" {
			name := rel["PRETTY_NAME"]
			if name == "" {
				name = "RHEL family " + ver
			}
			return osBestEffort, name
		}
	}
	return osUnsupported, ""
}

func parseOSRelease(b []byte) map[string]string {
	out := map[string]string{}
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, found := strings.Cut(line, "=")
		if !found {
			continue
		}
		out[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"'`)
	}
	return out
}

func (s *HostCheck) arch(ctx context.Context) (string, bool) {
	out, err := run(ctx, s.h, "uname", "-m")
	m := strings.TrimSpace(string(out))
	if err != nil {
		return "unknown", false
	}
	switch m {
	case "x86_64", "amd64":
		return "amd64", true
	case "aarch64", "arm64":
		return "arm64", true
	}
	return m, false
}

func hasCgroup2(mountinfo []byte) bool {
	sc := bufio.NewScanner(bytes.NewReader(mountinfo))
	for sc.Scan() {
		if strings.Contains(sc.Text(), " - cgroup2 ") {
			return true
		}
	}
	return false
}

func (s *HostCheck) cpus() int {
	b, err := s.h.ReadFile("/proc/cpuinfo")
	if err != nil {
		return 0
	}
	n := 0
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		if strings.HasPrefix(sc.Text(), "processor") {
			n++
		}
	}
	return n
}

// diskFreeKiB reads the Available column of `df -Pk /var/lib` (POSIX output:
// one header line, then one data line).
func (s *HostCheck) diskFreeKiB(ctx context.Context) (int64, error) {
	out, err := run(ctx, s.h, "df", "-Pk", "/var/lib")
	if err != nil {
		return 0, err
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) < 2 {
		return 0, fmt.Errorf("unexpected df output")
	}
	f := strings.Fields(lines[len(lines)-1])
	if len(f) < 4 {
		return 0, fmt.Errorf("unexpected df output")
	}
	v, err := strconv.ParseInt(f[3], 10, 64)
	if err != nil || v < 0 {
		return 0, fmt.Errorf("unexpected df output")
	}
	return v, nil
}

func (s *HostCheck) memoryKiB() (int64, error) {
	b, err := s.h.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, err
	}
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) >= 2 && f[0] == "MemTotal:" {
			v, err := strconv.ParseInt(f[1], 10, 64)
			if err != nil || v <= 0 {
				break
			}
			return v, nil
		}
	}
	return 0, fmt.Errorf("MemTotal not found")
}

// ----------------------------------------------------------------- PortFree

// PortFree refuses when something other than our own SimCore listens on the
// chosen TCP port. A listener is ours only when its process sits in the
// povengine-simcore container's cgroup: SimCore runs with --network host, so
// its port never shows in `docker ps`, and the container merely existing says
// nothing about who holds this port.
type PortFree struct {
	checkOnly
	h host.Host
}

func NewPortFree(h host.Host) *PortFree { return &PortFree{h: h} }

func (*PortFree) ID() string    { return "ports" }
func (*PortFree) Title() string { return "Port" }

func (s *PortFree) Check(ctx context.Context, st *engine.State) engine.Result {
	port := st.Choices.Port
	if port == 0 {
		port = defaultPort
	}
	inUse := fmt.Sprintf("TCP %d is already in use; stop what holds it or choose another port (--port)", port)

	if _, err := s.h.LookPath("ss"); err == nil {
		if out, err := run(ctx, s.h, "ss", "-Hltnp"); err == nil {
			ls := ssListeners(out, port)
			if len(ls) == 0 {
				return ok(fmt.Sprintf("TCP %d is free", port))
			}
			if s.allOurs(ctx, ls) {
				return ok(fmt.Sprintf("TCP %d is held by this installer's own SimCore", port))
			}
			return block(engine.CodePortInUse, inUse)
		}
	}

	// No usable ss: the socket table can say a port is busy but not whose it is.
	known := false
	for _, f := range []string{"/proc/net/tcp", "/proc/net/tcp6"} {
		b, err := s.h.ReadFile(f)
		if err != nil {
			continue
		}
		known = true
		if portInProcNet(b, port) {
			return block(engine.CodePortInUse, fmt.Sprintf(
				"TCP %d is in use and ss is not available, so the listener cannot be attributed to SimCore; stop what holds it or choose another port (--port)", port))
		}
	}
	if !known {
		return block(engine.CodePortInUse, fmt.Sprintf("cannot tell whether TCP %d is free: neither ss nor /proc/net/tcp is readable", port))
	}
	return ok(fmt.Sprintf("TCP %d is free", port))
}

// allOurs reports whether every listener on the port has a process inside the
// povengine-simcore container's cgroup.
func (s *PortFree) allOurs(ctx context.Context, ls []listener) bool {
	id := s.containerID(ctx)
	if id == "" {
		return false
	}
	for _, l := range ls {
		mine := false
		for _, pid := range l.pids {
			cg, err := s.h.ReadFile("/proc/" + strconv.Itoa(pid) + "/cgroup")
			if err == nil && strings.Contains(string(cg), id) {
				mine = true
				break
			}
		}
		if !mine {
			return false
		}
	}
	return true
}

// containerID asks the installer's private engine for povengine-simcore's id,
// preferring the bundled docker CLI and falling back to PATH. "" = not found.
func (s *PortFree) containerID(ctx context.Context) string {
	docker := "docker"
	if _, err := s.h.Stat(ownEngineDocker); err == nil {
		docker = ownEngineDocker
	}
	out, err := run(ctx, s.h, docker, "-H", ownEngineSocket, "inspect", "--format", "{{.Id}}", ownContainerName)
	if err != nil {
		return ""
	}
	id := strings.TrimSpace(string(out))
	if len(id) < 12 || strings.ContainsAny(id, " \t\n") {
		return ""
	}
	return id
}

// listener is one `ss` line for the port; pids is empty when ss could not name
// the process (not root).
type listener struct{ pids []int }

var pidRE = regexp.MustCompile(`pid=(\d+)`)

// ssListeners parses `ss -Hltnp`: State Recv-Q Send-Q Local:Port Peer:Port
// [users:(("name",pid=N,fd=M),...)] and returns the lines on port.
func ssListeners(out []byte, port int) []listener {
	var ls []listener
	sc := bufio.NewScanner(bytes.NewReader(out))
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 4 {
			continue
		}
		local := f[3]
		i := strings.LastIndex(local, ":")
		if i < 0 {
			continue
		}
		if p, err := strconv.Atoi(local[i+1:]); err != nil || p != port {
			continue
		}
		var l listener
		for _, m := range pidRE.FindAllStringSubmatch(strings.Join(f[4:], " "), -1) {
			if n, err := strconv.Atoi(m[1]); err == nil {
				l.pids = append(l.pids, n)
			}
		}
		ls = append(ls, l)
	}
	return ls
}

func portInSS(out []byte, port int) bool { return len(ssListeners(out, port)) > 0 }

// portInProcNet parses /proc/net/tcp or tcp6: only state 0A (LISTEN) counts.
func portInProcNet(b []byte, port int) bool {
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		f := strings.Fields(sc.Text())
		if len(f) < 4 || f[3] != "0A" {
			continue
		}
		i := strings.LastIndex(f[1], ":")
		if i < 0 {
			continue
		}
		if p, err := strconv.ParseInt(f[1][i+1:], 16, 32); err == nil && int(p) == port {
			return true
		}
	}
	return false
}

// ------------------------------------------------------------- ComposeAbsent

// ComposeAbsent refuses when a compose deployment of POVengine runs on the
// host's own Docker engine (§10): v1 does not import its database.
type ComposeAbsent struct {
	checkOnly
	h host.Host
}

func NewComposeAbsent(h host.Host) *ComposeAbsent { return &ComposeAbsent{h: h} }

func (*ComposeAbsent) ID() string    { return "compose" }
func (*ComposeAbsent) Title() string { return "Existing deployment" }

func (s *ComposeAbsent) Check(ctx context.Context, _ *engine.State) engine.Result {
	const none = "no other Docker engine"
	if _, err := s.h.LookPath("docker"); err != nil {
		return ok(none)
	}
	out, err := run(ctx, s.h, "docker", "ps", "--format", "{{.Names}}")
	if err != nil {
		return ok(none)
	}
	var found []string
	for _, n := range strings.Fields(string(out)) {
		if strings.HasPrefix(n, composeNamePrefix) {
			found = append(found, n)
		}
	}
	if len(found) == 0 {
		return ok("no existing compose deployment")
	}
	return block(engine.CodeComposeDeploymentPresent,
		fmt.Sprintf("%s is running; stop it first: %s", strings.Join(found, ", "), composeStopCommand))
}
