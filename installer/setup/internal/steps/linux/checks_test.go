package linux

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

func td(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// healthyHost is an Ubuntu 24.04 amd64 box with systemd, cgroup v2, 8 GiB
// and 38 GiB free. Tests override single entries to break one property.
func healthyHost(t *testing.T) *host.Fake {
	t.Helper()
	return &host.Fake{
		Files: map[string][]byte{
			"/etc/os-release":      td(t, "os-release-ubuntu2404"),
			"/proc/meminfo":        td(t, "meminfo-8g"),
			"/proc/cpuinfo":        td(t, "cpuinfo-4"),
			"/proc/self/mountinfo": td(t, "mountinfo-cgroup2"),
			"/proc/1/comm":         []byte("systemd\n"),
		},
		Responses: map[string]host.FakeResponse{
			"uname -m":        {Stdout: []byte("x86_64\n")},
			"df -Pk /var/lib": {Stdout: td(t, "df-38g")},
		},
	}
}

func newState() *engine.State {
	return &engine.State{Facts: map[string]string{}}
}

func check(t *testing.T, s engine.Step, st *engine.State) engine.Result {
	t.Helper()
	return s.Check(context.Background(), st)
}

func TestHostCheck_Ubuntu2404Passes(t *testing.T) {
	st := newState()
	r := check(t, NewHostCheck(healthyHost(t)), st)
	if r.Code != engine.CodeOK || r.Warning {
		t.Fatalf("got %+v, want plain OK", r)
	}
	if st.Facts[FactOS] != "Ubuntu 24.04.1 LTS" || st.Facts[FactArch] != "amd64" {
		t.Errorf("facts = %v", st.Facts)
	}
	if st.Facts[FactCPUs] != "4" {
		t.Errorf("cpus = %q", st.Facts[FactCPUs])
	}
}

func TestHostCheck_Ubuntu2204Passes(t *testing.T) {
	h := healthyHost(t)
	h.Files["/etc/os-release"] = []byte("ID=ubuntu\nVERSION_ID=\"22.04\"\nPRETTY_NAME=\"Ubuntu 22.04.4 LTS\"\n")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeOK || r.Warning {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_Rhel9IsBestEffortWarning(t *testing.T) {
	for _, f := range []string{"os-release-rhel9", "os-release-rocky9"} {
		h := healthyHost(t)
		h.Files["/etc/os-release"] = td(t, f)
		r := check(t, NewHostCheck(h), newState())
		if r.Code != engine.CodeOSUnsupported || !r.Warning || r.Blocks() {
			t.Fatalf("%s: got %+v, want OS_UNSUPPORTED warning", f, r)
		}
		if !strings.Contains(r.Detail, "best effort") {
			t.Errorf("%s: detail %q lacks 'best effort'", f, r.Detail)
		}
	}
}

func TestHostCheck_Debian11Refused(t *testing.T) {
	h := healthyHost(t)
	h.Files["/etc/os-release"] = td(t, "os-release-debian11")
	r := check(t, NewHostCheck(h), newState())
	if r.Code != engine.CodeOSUnsupported || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_OtherUbuntuAndRhel8Refused(t *testing.T) {
	h := healthyHost(t)
	h.Files["/etc/os-release"] = td(t, "os-release-ubuntu2004")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeOSUnsupported || !r.Blocks() {
		t.Fatalf("ubuntu 20.04: %+v", r)
	}
	h.Files["/etc/os-release"] = []byte("ID=rhel\nVERSION_ID=\"8.9\"\n")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeOSUnsupported || !r.Blocks() {
		t.Fatalf("rhel 8: %+v", r)
	}
}

func TestHostCheck_MissingOsReleaseRefused(t *testing.T) {
	h := healthyHost(t)
	delete(h.Files, "/etc/os-release")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeOSUnsupported || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_Arch(t *testing.T) {
	h := healthyHost(t)
	h.Responses["uname -m"] = host.FakeResponse{Stdout: []byte("aarch64\n")}
	st := newState()
	if r := check(t, NewHostCheck(h), st); r.Code != engine.CodeOK || st.Facts[FactArch] != "arm64" {
		t.Fatalf("arm64: %+v facts=%v", r, st.Facts)
	}
	h.Responses["uname -m"] = host.FakeResponse{Stdout: []byte("riscv64\n")}
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeArchUnsupported || !r.Blocks() {
		t.Fatalf("riscv64: %+v", r)
	}
}

func TestHostCheck_NoSystemdRefused(t *testing.T) {
	h := healthyHost(t)
	h.Files["/proc/1/comm"] = []byte("init\n")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeNoSystemd || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_NoCgroupV2Refused(t *testing.T) {
	h := healthyHost(t)
	h.Files["/proc/self/mountinfo"] = td(t, "mountinfo-cgroup1")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeNoCgroupV2 || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_DiskLow(t *testing.T) {
	h := healthyHost(t)
	h.Responses["df -Pk /var/lib"] = host.FakeResponse{Stdout: td(t, "df-3g")}
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeDiskLow || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_DiskUnreadableRefused(t *testing.T) {
	h := healthyHost(t)
	delete(h.Responses, "df -Pk /var/lib")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeDiskLow || !r.Blocks() {
		t.Fatalf("an unreadable disk size must not pass: %+v", r)
	}
}

func TestHostCheck_MemoryLow(t *testing.T) {
	h := healthyHost(t)
	h.Files["/proc/meminfo"] = td(t, "meminfo-2g")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeMemoryLow || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_BlockBeatsRhelWarning(t *testing.T) {
	h := healthyHost(t)
	h.Files["/etc/os-release"] = td(t, "os-release-rhel9")
	h.Files["/proc/meminfo"] = td(t, "meminfo-2g")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeMemoryLow || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
}

func TestHostCheck_ApplyChangesNothing(t *testing.T) {
	h := healthyHost(t)
	s := NewHostCheck(h)
	if r := s.Apply(context.Background(), newState()); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
	if len(h.Calls) != 0 {
		t.Errorf("Apply touched the host: %v", h.Calls)
	}
}

const (
	ownInspect = "docker -H unix:///run/povengine/docker.sock inspect --format {{.Id}} povengine-simcore"
	ownID      = "3f9c1d2e7a4b5c6d8e9f0a1b2c3d4e5f60718293a4b5c6d7e8f90123456789ab"
)

func TestParseSS(t *testing.T) {
	if !portInSS(td(t, "ss-listen-8888.txt"), 8888) {
		t.Error("8888 should be listening")
	}
	if portInSS(td(t, "ss-listen-none.txt"), 8888) {
		t.Error("8888 should be free")
	}
	// A port that is a suffix of another must not match, and v6 / scoped forms parse.
	if portInSS(td(t, "ss-listen-8888.txt"), 888) {
		t.Error("888 matched 8888")
	}
	if !portInSS(td(t, "ss-listen-8888.txt"), 53) {
		t.Error("scoped 127.0.0.53%lo:53 not parsed")
	}
	if !portInSS(td(t, "ss-listen-8888.txt"), 22) {
		t.Error("[::]:22 not parsed")
	}
}

func TestParseProcNetTCP(t *testing.T) {
	if !portInProcNet(td(t, "proc-net-tcp"), 8888) {
		t.Error("0A listener on 8888 missed")
	}
	if portInProcNet(td(t, "proc-net-tcp-no8888"), 8888) {
		t.Error("an ESTABLISHED (01) socket on 8888 must not count as a listener")
	}
	if !portInProcNet(td(t, "proc-net-tcp6"), 22) {
		t.Error("tcp6 listener missed")
	}
}

func ssHost(t *testing.T, ssFile string) *host.Fake {
	t.Helper()
	return &host.Fake{
		Paths: map[string]string{"ss": "/usr/bin/ss"},
		Files: map[string][]byte{
			"/proc/4242/cgroup": []byte("0::/system.slice/docker-" + ownID + ".scope\n"),
			"/proc/999/cgroup":  []byte("0::/system.slice/nginx.service\n"),
		},
		Responses: map[string]host.FakeResponse{
			"ss -Hltnp": {Stdout: td(t, ssFile)},
			ownInspect:  {Stdout: []byte(ownID + "\n")},
		},
	}
}

func port8888() *engine.State {
	st := newState()
	st.Choices.Port = 8888
	return st
}

func TestPortFree_FreePort(t *testing.T) {
	if r := check(t, NewPortFree(ssHost(t, "ss-listen-none.txt")), port8888()); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
}

func TestPortFree_OwnContainerIsFine(t *testing.T) {
	if r := check(t, NewPortFree(ssHost(t, "ss-listen-8888-ours.txt")), port8888()); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
}

func TestPortFree_StrangerWhileOurContainerRuns(t *testing.T) {
	r := check(t, NewPortFree(ssHost(t, "ss-listen-8888-stranger.txt")), port8888())
	if r.Code != engine.CodePortInUse || !r.Blocks() || !strings.Contains(r.Detail, "8888") {
		t.Fatalf("a stranger on the port must refuse even though povengine-simcore runs: %+v", r)
	}
}

func TestPortFree_NoPidInfoIsNotOurs(t *testing.T) {
	if r := check(t, NewPortFree(ssHost(t, "ss-listen-8888-nopid.txt")), port8888()); r.Code != engine.CodePortInUse {
		t.Fatalf("got %+v", r)
	}
}

func TestPortFree_DockerFailureMeansNotOurs(t *testing.T) {
	h := ssHost(t, "ss-listen-8888-ours.txt")
	h.Responses[ownInspect] = host.FakeResponse{Err: errors.New("no such object")}
	if r := check(t, NewPortFree(h), port8888()); r.Code != engine.CodePortInUse {
		t.Fatalf("got %+v", r)
	}
}

func TestPortFree_FallbackWithListenerCannotAttribute(t *testing.T) {
	h := &host.Fake{Files: map[string][]byte{
		"/proc/net/tcp":  td(t, "proc-net-tcp"),
		"/proc/net/tcp6": td(t, "proc-net-tcp6"),
	}}
	r := check(t, NewPortFree(h), port8888())
	if r.Code != engine.CodePortInUse || !r.Blocks() || !strings.Contains(r.Detail, "cannot be attributed") {
		t.Fatalf("got %+v", r)
	}
	st := newState()
	st.Choices.Port = 9999
	if r := check(t, NewPortFree(h), st); r.Code != engine.CodeOK {
		t.Fatalf("free via /proc: %+v", r)
	}
}

func TestPortFree_SSFailureFallsBack(t *testing.T) {
	h := &host.Fake{
		Paths:     map[string]string{"ss": "/usr/bin/ss"},
		Files:     map[string][]byte{"/proc/net/tcp": td(t, "proc-net-tcp-no8888")},
		Responses: map[string]host.FakeResponse{"ss -Hltnp": {Err: errors.New("boom")}},
	}
	if r := check(t, NewPortFree(h), port8888()); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
}

func TestPortFree_NothingReadableIsNotAPass(t *testing.T) {
	r := check(t, NewPortFree(&host.Fake{}), port8888())
	if !r.Blocks() || r.Code != engine.CodePortInUse {
		t.Fatalf("an unreadable socket table must not read as free: %+v", r)
	}
}

func TestPortFree_DefaultPort(t *testing.T) {
	if r := check(t, NewPortFree(ssHost(t, "ss-listen-8888-stranger.txt")), newState()); r.Code != engine.CodePortInUse {
		t.Fatalf("port 0 must mean 8888: %+v", r)
	}
}

func TestComposeAbsent_NamesTheContainer(t *testing.T) {
	h := &host.Fake{
		Paths: map[string]string{"docker": "/usr/bin/docker"},
		Responses: map[string]host.FakeResponse{
			"docker ps --format {{.Names}}": {Stdout: []byte("web\ncortex-pov-engine-simcore-1\n")},
		},
	}
	r := check(t, NewComposeAbsent(h), newState())
	if r.Code != engine.CodeComposeDeploymentPresent || !r.Blocks() {
		t.Fatalf("got %+v", r)
	}
	for _, want := range []string{"cortex-pov-engine-simcore-1", "docker compose -p cortex-pov-engine stop"} {
		if !strings.Contains(r.Detail, want) {
			t.Errorf("detail %q lacks %q", r.Detail, want)
		}
	}
}

func TestComposeAbsent_OtherContainersAreFine(t *testing.T) {
	h := &host.Fake{
		Paths:     map[string]string{"docker": "/usr/bin/docker"},
		Responses: map[string]host.FakeResponse{"docker ps --format {{.Names}}": {Stdout: []byte("web\ndb\n")}},
	}
	if r := check(t, NewComposeAbsent(h), newState()); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
}

func TestComposeAbsent_NoDockerOrNoSocket(t *testing.T) {
	if r := check(t, NewComposeAbsent(&host.Fake{}), newState()); r.Code != engine.CodeOK || r.Detail != "no other Docker engine" {
		t.Fatalf("no docker: %+v", r)
	}
	h := &host.Fake{
		Paths:     map[string]string{"docker": "/usr/bin/docker"},
		Responses: map[string]host.FakeResponse{"docker ps --format {{.Names}}": {Err: errors.New("cannot connect to the Docker daemon")}},
	}
	if r := check(t, NewComposeAbsent(h), newState()); r.Code != engine.CodeOK || r.Detail != "no other Docker engine" {
		t.Fatalf("daemon down: %+v", r)
	}
}

func TestAddressCandidates_DefaultRouteFirst(t *testing.T) {
	got, err := AddressCandidates(td(t, "ip-route-two-nics.json"), td(t, "ip-addr-two-nics.json"))
	if err != nil {
		t.Fatal(err)
	}
	// ens3 holds the lowest-metric default route, so 192.0.2.10 leads even
	// though ens4 is listed first; the global IPv6 address comes last.
	want := []string{"192.0.2.10", "198.51.100.20", "2001:db8:1::20"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestAddressCandidates_SkipsLinkLocal(t *testing.T) {
	got, err := AddressCandidates(td(t, "ip-route-two-nics.json"), td(t, "ip-addr-two-nics.json"))
	if err != nil {
		t.Fatal(err)
	}
	for _, a := range got {
		if strings.HasPrefix(a, "169.254.") || strings.HasPrefix(a, "fe80") || strings.HasPrefix(a, "127.") || a == "::1" {
			t.Errorf("candidate %q must never be offered", a)
		}
	}
}

func TestAddressCandidates_NoDefaultRoute(t *testing.T) {
	got, err := AddressCandidates([]byte(`[]`), td(t, "ip-addr-two-nics.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 3 {
		t.Fatalf("got %v", got)
	}
}

func TestAddressCandidates_BadJSONRaises(t *testing.T) {
	if _, err := AddressCandidates([]byte(`{"nope":1}`), td(t, "ip-addr-two-nics.json")); err == nil {
		t.Error("an unrecognised route shape must be an error")
	}
	if _, err := AddressCandidates([]byte(`[]`), []byte(`not json`)); err == nil {
		t.Error("an unrecognised addr shape must be an error")
	}
}

func addressHost(t *testing.T) *host.Fake {
	return &host.Fake{Responses: map[string]host.FakeResponse{
		"ip -j route show default":    {Stdout: td(t, "ip-route-two-nics.json")},
		"ip -j -6 route show default": {Stdout: []byte(`[]`)},
		"ip -j addr":                  {Stdout: td(t, "ip-addr-two-nics.json")},
	}}
}

func TestAddressStep_DefaultsToFirstCandidate(t *testing.T) {
	st := newState()
	r := check(t, NewAddressStep(addressHost(t)), st)
	if r.Code != engine.CodeOK || st.Choices.Address != "192.0.2.10" {
		t.Fatalf("got %+v address=%q", r, st.Choices.Address)
	}
	if st.Facts[FactAddressCandidates] != "192.0.2.10,198.51.100.20,2001:db8:1::20" {
		t.Errorf("candidates = %q", st.Facts[FactAddressCandidates])
	}
}

func TestAddressStep_ValidatesGivenAddress(t *testing.T) {
	cases := []struct {
		addr string
		ok   bool
	}{
		{"192.0.2.10", true},
		{"2001:db8::5", true},
		{"jumpbox", true},
		{"jump-box.lab.example", true},
		{"JumpBox.Lab.Example", true},
		{"http://192.0.2.10", false},
		{"192.0.2.10:8888", false},
		{"jump box", false},
		{"jumpbox:8888", false},
		{"-bad.example", false},
		{"bad-.example", false},
		{"under_score.example", false},
		{"a..b", false},
		{"999.1.1.1", false},
		{"[2001:db8::5]", false},
		{strings.Repeat("a", 64) + ".example", false},
	}
	for _, c := range cases {
		st := newState()
		st.Choices.Address = c.addr
		r := check(t, NewAddressStep(addressHost(t)), st)
		if c.ok && r.Code != engine.CodeOK {
			t.Errorf("%q: want OK, got %+v", c.addr, r)
		}
		if !c.ok && (r.Code != engine.CodeAddressInvalid || !r.Blocks()) {
			t.Errorf("%q: want ADDRESS_INVALID, got %+v", c.addr, r)
		}
		if c.ok && st.Choices.Address != c.addr {
			t.Errorf("%q: a given address must not be replaced (%q)", c.addr, st.Choices.Address)
		}
	}
}

func TestAddressStep_ListenOnlyNeedsAnIP(t *testing.T) {
	st := newState()
	st.Choices.Address = "jumpbox.lab.example"
	st.Choices.ListenOnly = true
	if r := check(t, NewAddressStep(addressHost(t)), st); r.Code != engine.CodeAddressInvalid {
		t.Fatalf("got %+v", r)
	}
	st.Choices.Address = "192.0.2.10"
	if r := check(t, NewAddressStep(addressHost(t)), st); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
}

func TestAddressStep_NoCandidatesNoAddress(t *testing.T) {
	h := &host.Fake{Responses: map[string]host.FakeResponse{
		"ip -j route show default": {Stdout: []byte(`[]`)},
		"ip -j addr":               {Stdout: []byte(`[]`)},
	}}
	r := check(t, NewAddressStep(h), newState())
	if r.Code != engine.CodeAddressInvalid || !strings.Contains(r.Detail, "--address") {
		t.Fatalf("got %+v", r)
	}
	// A given address still passes when detection finds nothing.
	st := newState()
	st.Choices.Address = "192.0.2.10"
	if r := check(t, NewAddressStep(h), st); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
	// And so does one when ip is missing altogether.
	st = newState()
	st.Choices.Address = "192.0.2.10"
	if r := check(t, NewAddressStep(&host.Fake{}), st); r.Code != engine.CodeOK {
		t.Fatalf("ip absent: %+v", r)
	}
}

func TestDetectProxy_EnvWinsOverEtcEnvironment(t *testing.T) {
	url, pac := DetectProxy([]string{"PATH=/usr/bin", "HTTPS_PROXY=http://env-proxy.example.test:8080"}, td(t, "etc-environment"))
	if url != "http://env-proxy.example.test:8080" || pac {
		t.Fatalf("got %q pac=%v", url, pac)
	}
}

func TestDetectProxy_FallsBackToEtcEnvironment(t *testing.T) {
	url, pac := DetectProxy([]string{"PATH=/usr/bin"}, td(t, "etc-environment"))
	if url != "http://proxy.example.test:3128" || pac {
		t.Fatalf("got %q pac=%v", url, pac)
	}
}

func TestDetectProxy_HTTPSBeatsHTTPAndLowerCaseWorks(t *testing.T) {
	url, _ := DetectProxy([]string{"http_proxy=http://a.example.test:1", "https_proxy=http://b.example.test:2"}, nil)
	if url != "http://b.example.test:2" {
		t.Fatalf("got %q", url)
	}
	url, _ = DetectProxy([]string{"http_proxy=http://a.example.test:1"}, nil)
	if url != "http://a.example.test:1" {
		t.Fatalf("got %q", url)
	}
}

func TestDetectProxy_NothingFound(t *testing.T) {
	if url, pac := DetectProxy([]string{"PATH=/usr/bin", "HTTPS_PROXY="}, []byte("PATH=/usr/bin\n")); url != "" || pac {
		t.Fatalf("got %q pac=%v", url, pac)
	}
}

func TestDetectProxy_PacIsFlagged(t *testing.T) {
	url, pac := DetectProxy(nil, td(t, "etc-environment-pac"))
	if !pac || url != "" {
		t.Fatalf("auto_proxy: got %q pac=%v", url, pac)
	}
	url, pac = DetectProxy([]string{"HTTPS_PROXY=http://wpad.example.test/proxy.pac"}, nil)
	if !pac || url != "" {
		t.Fatalf(".pac url: got %q pac=%v", url, pac)
	}
}

func TestProxyStep_UsesDetectedProxy(t *testing.T) {
	h := &host.Fake{Files: map[string][]byte{"/etc/environment": td(t, "etc-environment")}}
	st := newState()
	r := check(t, NewProxyStep(h, nil), st)
	if r.Code != engine.CodeOK || st.Facts[FactProxy] != "http://proxy.example.test:3128" {
		t.Fatalf("got %+v facts=%v", r, st.Facts)
	}
}

func TestProxyStep_NoneAndNothingFound(t *testing.T) {
	st := newState()
	st.Choices.Proxy = "none"
	h := &host.Fake{Files: map[string][]byte{"/etc/environment": td(t, "etc-environment")}}
	if r := check(t, NewProxyStep(h, nil), st); r.Code != engine.CodeOK || st.Facts[FactProxy] != "" {
		t.Fatalf("none: %+v facts=%v", r, st.Facts)
	}
	st = newState()
	if r := check(t, NewProxyStep(&host.Fake{}, nil), st); r.Code != engine.CodeOK || st.Facts[FactProxy] != "" {
		t.Fatalf("nothing: %+v facts=%v", r, st.Facts)
	}
}

func TestProxyStep_GivenProxyWinsOverDetected(t *testing.T) {
	st := newState()
	st.Choices.Proxy = "http://given.example.test:3128"
	h := &host.Fake{Files: map[string][]byte{"/etc/environment": td(t, "etc-environment")}}
	r := check(t, NewProxyStep(h, nil), st)
	if r.Code != engine.CodeOK || st.Facts[FactProxy] != "http://given.example.test:3128" {
		t.Fatalf("got %+v facts=%v", r, st.Facts)
	}
}

func TestProxyStep_PacWarns(t *testing.T) {
	h := &host.Fake{Files: map[string][]byte{"/etc/environment": td(t, "etc-environment-pac")}}
	st := newState()
	r := check(t, NewProxyStep(h, nil), st)
	if r.Code != engine.CodeProxyPACUnsupported || !r.Warning || r.Blocks() {
		t.Fatalf("got %+v", r)
	}
	if st.Facts[FactProxyPAC] != "true" || st.Facts[FactProxy] != "" {
		t.Errorf("facts = %v", st.Facts)
	}
}

func TestProxyStep_CredentialsBlockWithoutLeaking(t *testing.T) {
	const secret = "s3cr3t-pw"
	for name, run := range map[string]func() (engine.Result, *engine.State){
		"given": func() (engine.Result, *engine.State) {
			st := newState()
			st.Choices.Proxy = "http://alice:" + secret + "@proxy.example.test:3128"
			return check(t, NewProxyStep(&host.Fake{}, nil), st), st
		},
		"detected": func() (engine.Result, *engine.State) {
			st := newState()
			return check(t, NewProxyStep(&host.Fake{}, []string{"HTTPS_PROXY=http://alice:" + secret + "@proxy.example.test:3128"}), st), st
		},
	} {
		r, st := run()
		if r.Code != engine.CodeProxyAuthUnsupported || !r.Blocks() {
			t.Fatalf("%s: got %+v", name, r)
		}
		if strings.Contains(r.Detail, secret) || strings.Contains(r.Detail, "alice") {
			t.Errorf("%s: detail leaks credentials: %q", name, r.Detail)
		}
		for k, v := range st.Facts {
			if strings.Contains(v, secret) {
				t.Errorf("%s: fact %s leaks credentials", name, k)
			}
		}
	}
}

func TestProxyStep_SchemelessGivenProxy(t *testing.T) {
	st := newState()
	st.Choices.Proxy = "proxy.example.test:3128"
	if r := check(t, NewProxyStep(&host.Fake{}, nil), st); r.Code != engine.CodeOK || st.Facts[FactProxy] != "http://proxy.example.test:3128" {
		t.Fatalf("got %+v facts=%v", r, st.Facts)
	}
}

func TestEndpointProtection_XdrWarns(t *testing.T) {
	h := &host.Fake{Files: map[string][]byte{"/opt/traps/bin/placeholder": nil}}
	st := newState()
	r := check(t, NewEndpointProtection(h), st)
	if r.Code != engine.CodeEndpointProtectionExclusionNeeded || !r.Warning || r.Blocks() {
		t.Fatalf("got %+v", r)
	}
	for _, p := range []string{"/opt/povengine", "/var/lib/povengine"} {
		if !strings.Contains(r.Detail, p) {
			t.Errorf("detail %q lacks %s", r.Detail, p)
		}
	}
	if st.Facts[FactEndpointProtection] != "cortex-xdr" {
		t.Errorf("facts = %v", st.Facts)
	}
}

func TestEndpointProtection_ProcessOnly(t *testing.T) {
	h := &host.Fake{Responses: map[string]host.FakeResponse{"pgrep -x traps_pmd": {Stdout: []byte("812\n")}}}
	if r := check(t, NewEndpointProtection(h), newState()); r.Code != engine.CodeEndpointProtectionExclusionNeeded || !r.Warning {
		t.Fatalf("got %+v", r)
	}
}

func TestEndpointProtection_NoneFound(t *testing.T) {
	// No /opt/traps, pgrep exits 1 (no match) or is missing: both read as none.
	for name, h := range map[string]*host.Fake{
		"no match":      {Responses: map[string]host.FakeResponse{"pgrep -x traps_pmd": {Err: errors.New("exit status 1")}}},
		"pgrep missing": {},
	} {
		st := newState()
		r := check(t, NewEndpointProtection(h), st)
		if r.Code != engine.CodeOK || r.Warning || st.Facts[FactEndpointProtection] != "none" {
			t.Errorf("%s: got %+v facts=%v", name, r, st.Facts)
		}
	}
}

func TestStepsAreCheckOnly(t *testing.T) {
	h := healthyHost(t)
	steps := []engine.Step{NewHostCheck(h), NewPortFree(h), NewComposeAbsent(h), NewAddressStep(h), NewProxyStep(h, nil), NewEndpointProtection(h)}
	seen := map[string]bool{}
	for _, s := range steps {
		if s.ID() == "" || s.Title() == "" || seen[s.ID()] {
			t.Errorf("step %T has a bad or duplicate id/title: %q %q", s, s.ID(), s.Title())
		}
		seen[s.ID()] = true
		if r := s.Apply(context.Background(), newState()); r.Code != engine.CodeOK {
			t.Errorf("%s Apply = %+v", s.ID(), r)
		}
	}
	for _, c := range h.Calls {
		if c.Method == "WriteFile" || c.Method == "MkdirAll" || c.Method == "RemoveAll" {
			t.Errorf("a check-only step wrote: %+v", c)
		}
	}
}

func TestHostCheck_MemoryFloor(t *testing.T) {
	h := healthyHost(t)
	h.Files["/proc/meminfo"] = []byte("MemTotal:        3880000 kB\n")
	if r := check(t, NewHostCheck(h), newState()); r.Code != engine.CodeOK {
		t.Fatalf("a nominal 4 GB machine must pass: %+v", r)
	}
	h.Files["/proc/meminfo"] = []byte("MemTotal:        3600000 kB\n")
	r := check(t, NewHostCheck(h), newState())
	if r.Code != engine.CodeMemoryLow || !r.Blocks() || !strings.Contains(r.Detail, "3.4 GiB") {
		t.Fatalf("got %+v", r)
	}
}

func TestAddressCandidates_ContainerInterfacesNeverListed(t *testing.T) {
	got, err := AddressCandidates(td(t, "ip-route-docker.json"), td(t, "ip-addr-docker.json"))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, []string{"192.0.2.10"}) {
		t.Fatalf("got %v, want only the ens3 address", got)
	}
}

func TestAddressCandidates_IPv6OnlyDefaultRoute(t *testing.T) {
	got, err := addressCandidates([][]byte{[]byte(`[]`), td(t, "ip-route6-default.json")}, td(t, "ip-addr-v6only.json"))
	if err != nil {
		t.Fatal(err)
	}
	// ens6 holds the IPv6 default route, so it leads; docker0's IPv4 is dropped.
	want := []string{"2001:db8:6::7", "2001:db8:5::7"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestAddressCandidates_RankBeatsFamily(t *testing.T) {
	// Default-route NIC has only IPv6; another NIC has IPv4: the default-route
	// interface still leads.
	got, err := addressCandidates([][]byte{[]byte(`[]`), td(t, "ip-route6-default.json")}, []byte(`[
 {"ifname":"ens3","addr_info":[{"family":"inet","local":"192.0.2.10","scope":"global"}]},
 {"ifname":"ens6","addr_info":[{"family":"inet6","local":"2001:db8:6::7","scope":"global"}]}]`))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, []string{"2001:db8:6::7", "192.0.2.10"}) {
		t.Fatalf("got %v", got)
	}
}

func TestAddressCandidates_MultipathNexthops(t *testing.T) {
	got, err := AddressCandidates(td(t, "ip-route-multipath.json"), td(t, "ip-addr-two-nics.json"))
	if err != nil {
		t.Fatal(err)
	}
	// ens4 is the first nexthop: both its addresses (IPv4 then IPv6) rank
	// ahead of ens3, the second nexthop.
	want := []string{"198.51.100.20", "2001:db8:1::20", "192.0.2.10"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestAddressStep_ParseErrorIsInTheBlockDetail(t *testing.T) {
	h := &host.Fake{Responses: map[string]host.FakeResponse{
		"ip -j route show default":    {Stdout: []byte(`{"unexpected":true}`)},
		"ip -j -6 route show default": {Stdout: []byte(`[]`)},
		"ip -j addr":                  {Stdout: td(t, "ip-addr-two-nics.json")},
	}}
	r := check(t, NewAddressStep(h), newState())
	if r.Code != engine.CodeAddressInvalid || !r.Blocks() || !strings.Contains(r.Detail, "ip -j route") || !strings.Contains(r.Detail, "--address") {
		t.Fatalf("got %+v", r)
	}
	// A given address still passes despite the unreadable table.
	st := newState()
	st.Choices.Address = "192.0.2.10"
	if r := check(t, NewAddressStep(h), st); r.Code != engine.CodeOK {
		t.Fatalf("got %+v", r)
	}
}
