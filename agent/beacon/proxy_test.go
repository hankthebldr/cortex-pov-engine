package beacon

// BEACON-19 — the beacon's route to SimCore. See proxy.go for what is and is
// not done; these tests pin both halves: an explicit --proxy really carries
// every request the beacon makes, and the startup lines say what the beacon
// does NOT read.

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func TestParseProxySpec(t *testing.T) {
	ok := map[string]struct {
		url    string
		direct bool
	}{
		"":                               {"", false},
		"   ":                            {"", false},
		"direct":                         {"", true},
		"DIRECT":                         {"", true},
		"http://proxy.corp.example:8080": {"http://proxy.corp.example:8080", false},
		"https://proxy.corp.example":     {"https://proxy.corp.example", false},
		"socks5://127.0.0.1:1080":        {"socks5://127.0.0.1:1080", false},
		"http://user:secret@proxy:3128":  {"http://user:secret@proxy:3128", false},
	}
	for spec, want := range ok {
		u, direct, err := ParseProxySpec(spec)
		if err != nil {
			t.Errorf("ParseProxySpec(%q) = %v, want it accepted", spec, err)
			continue
		}
		got := ""
		if u != nil {
			got = u.String()
		}
		if got != want.url || direct != want.direct {
			t.Errorf("ParseProxySpec(%q) = (%q, direct=%v), want (%q, direct=%v)", spec, got, direct, want.url, want.direct)
		}
	}
	for _, spec := range []string{
		"proxy.corp.example:8080",        // no scheme: url.Parse reads "proxy.corp.example" as the scheme
		"ftp://proxy.corp.example",       // not a proxy scheme Go speaks
		"http://",                        // no host
		"http://:8080",                   // a port is not a host
		"none",                           // only "direct" is the keyword
		"http://user:secret@bad host:80", // unparseable
	} {
		_, _, err := ParseProxySpec(spec)
		if err == nil {
			t.Errorf("ParseProxySpec(%q) was accepted", spec)
			continue
		}
		if strings.Contains(err.Error(), "secret") {
			t.Errorf("the error for %q repeats the credential: %v", spec, err)
		}
	}
}

// forwardProxy is a plain HTTP forward proxy as far as the beacon can tell: it
// receives absolute-URI requests, records them, and answers itself.
type forwardProxy struct {
	*httptest.Server
	mu   sync.Mutex
	seen []string // "METHOD absolute-url"
	body []byte   // served for any GET of /api/shelf/payload/*
}

func newForwardProxy(t *testing.T, artifact []byte) *forwardProxy {
	t.Helper()
	p := &forwardProxy{body: artifact}
	p.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		p.mu.Lock()
		p.seen = append(p.seen, r.Method+" "+r.URL.String())
		p.mu.Unlock()
		if strings.Contains(r.URL.Path, "/api/shelf/payload/") {
			_, _ = w.Write(p.body)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"task":null}`))
	}))
	t.Cleanup(p.Close)
	return p
}

func (p *forwardProxy) requests() []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string(nil), p.seen...)
}

// simCoreBehindTheProxy is a SimCore address that does not resolve: a request
// that reaches the proxy for it can only have gone THROUGH the proxy.
const simCoreBehindTheProxy = "http://simcore.lab.invalid:8888"

// An explicit proxy carries every kind of request the beacon makes: the API
// POSTs, the task poll (and its long-poll clone of the client), and the
// artifact fetch, which uses a separate client.
func TestSetProxy_AnExplicitProxyCarriesEveryRequest(t *testing.T) {
	tool := []byte("#!/bin/sh\necho tool\n")
	proxy := newForwardProxy(t, tool)

	stageRoot := t.TempDir()
	c := stagingClient(t, simCoreBehindTheProxy, stageRoot)
	c.DisableArtifactCache = true
	if err := c.SetProxy(proxy.URL); err != nil {
		t.Fatalf("SetProxy: %v", err)
	}

	if err := c.Register("host", "linux", []string{"shell"}, nil); err != nil {
		t.Fatalf("Register through the proxy: %v", err)
	}
	if _, err := c.PollTasks(); err != nil {
		t.Fatalf("PollTasks through the proxy: %v", err)
	}
	if _, err := c.PollTasksWait(60); err != nil {
		t.Fatalf("long-poll through the proxy: %v", err)
	}
	if err := c.SendOutput("run-1", "step-01", "frame"); err != nil {
		t.Fatalf("SendOutput through the proxy: %v", err)
	}
	set := c.stageArtifacts(context.Background(), &Task{RunID: "run-1", Artifacts: []Artifact{{
		Name: "tool.sh", SHA256: digestOf(tool), Path: "/api/shelf/payload/tool.sh",
		Dest: filepath.Join(stageRoot, "run-1", "tool.sh"), Mode: "0755",
	}}})
	if !set.OK() {
		t.Fatalf("artifact fetch through the proxy failed: %+v", set.Failures)
	}
	defer set.Cleanup(c, "")
	if staged, err := os.ReadFile(filepath.Join(stageRoot, "run-1", "tool.sh")); err != nil || string(staged) != string(tool) {
		t.Fatalf("staged artifact = %q (err %v)", staged, err)
	}

	got := strings.Join(proxy.requests(), "\n")
	for _, want := range []string{
		"POST " + simCoreBehindTheProxy + "/api/agents/register",
		"GET " + simCoreBehindTheProxy + "/api/agents/agent-test/tasks",
		"GET " + simCoreBehindTheProxy + "/api/agents/agent-test/tasks?wait=60",
		"POST " + simCoreBehindTheProxy + "/api/runs/run-1/output",
		"GET " + simCoreBehindTheProxy + "/api/shelf/payload/tool.sh",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("the proxy never saw %q\nit saw:\n%s", want, got)
		}
	}
}

// The artifact client is built lazily: a proxy set BEFORE its first use must
// reach it too (the case above builds it first through stagingClient's
// helpers only if they touch it; this pins the other order explicitly).
func TestSetProxy_ReachesAnArtifactClientBuiltLater(t *testing.T) {
	c := New(simCoreBehindTheProxy, "a-1", 0)
	if err := c.SetProxy("http://proxy.corp.example:8080"); err != nil {
		t.Fatal(err)
	}
	if c.artifactClient().Transport != c.proxyTransport || c.http.Transport != c.proxyTransport {
		t.Fatal("the two HTTP clients do not share the --proxy transport")
	}
	// And the other order.
	c2 := New(simCoreBehindTheProxy, "a-1", 0)
	_ = c2.artifactClient()
	if err := c2.SetProxy("http://proxy.corp.example:8080"); err != nil {
		t.Fatal(err)
	}
	if c2.artifactClient().Transport != c2.proxyTransport {
		t.Fatal("a proxy set after the artifact client existed did not reach it")
	}
}

func TestSetProxy_DirectNeverProxiesAndEmptyChangesNothing(t *testing.T) {
	c := New(simCoreBehindTheProxy, "a-1", 0)
	if err := c.SetProxy(""); err != nil {
		t.Fatal(err)
	}
	if c.http.Transport != nil || c.proxyTransport != nil {
		t.Fatal("an empty --proxy replaced the default transport: an existing deployment's behaviour changed")
	}

	if err := c.SetProxy("direct"); err != nil {
		t.Fatal(err)
	}
	tr, ok := c.http.Transport.(*http.Transport)
	if !ok || tr.Proxy != nil {
		t.Fatalf("--proxy direct left a proxy function on the transport: %+v", c.http.Transport)
	}

	if err := New(simCoreBehindTheProxy, "a-1", 0).SetProxy("ftp://nope"); err == nil {
		t.Fatal("SetProxy accepted an unusable value")
	}
}

func answerForEnvironment(t *testing.T, u *url.URL, err error) {
	t.Helper()
	old := proxyFromEnvironment
	proxyFromEnvironment = func(*http.Request) (*url.URL, error) { return u, err }
	t.Cleanup(func() { proxyFromEnvironment = old })
}

// The startup lines: which route, and on Windows — when nobody chose one — the
// plain statement that the system proxy is not read.
func TestProxyStartupLines(t *testing.T) {
	const systemProxy = "Windows system proxy"
	join := func(c *BeaconClient, goos string) string { return strings.Join(c.ProxyStartupLines(goos), "\n") }

	t.Run("environment, nothing applies", func(t *testing.T) {
		answerForEnvironment(t, nil, nil)
		c := New("http://simcore:8888", "a-1", 0)
		for _, goos := range []string{"linux", "darwin"} {
			got := join(c, goos)
			if !strings.Contains(got, "http://simcore:8888") || !strings.Contains(got, "reached directly") {
				t.Errorf("%s: %q does not say SimCore is reached directly", goos, got)
			}
			if strings.Contains(got, systemProxy) {
				t.Errorf("%s: the Windows note was printed off Windows: %q", goos, got)
			}
		}
		got := join(c, "windows")
		for _, want := range []string{"reached directly", systemProxy, "NOT read", "Invoke-WebRequest", "--proxy"} {
			if !strings.Contains(got, want) {
				t.Errorf("windows: startup lines do not mention %q:\n%s", want, got)
			}
		}
	})

	t.Run("environment, a proxy applies", func(t *testing.T) {
		u, _ := url.Parse("http://envuser:envsecret@envproxy:3128")
		answerForEnvironment(t, u, nil)
		got := join(New("http://simcore:8888", "a-1", 0), "windows")
		if !strings.Contains(got, "envproxy:3128") || !strings.Contains(got, "environment") {
			t.Errorf("the environment proxy is not named: %q", got)
		}
		if strings.Contains(got, "envsecret") {
			t.Errorf("the proxy password was logged: %q", got)
		}
		if !strings.Contains(got, systemProxy) {
			t.Errorf("windows: the system-proxy note is missing in environment mode: %q", got)
		}
	})

	t.Run("environment is invalid", func(t *testing.T) {
		answerForEnvironment(t, nil, errors.New("invalid proxy address"))
		if got := join(New("http://simcore:8888", "a-1", 0), "linux"); !strings.Contains(got, "invalid") {
			t.Errorf("an invalid proxy environment is not reported: %q", got)
		}
	})

	t.Run("explicit", func(t *testing.T) {
		c := New("http://simcore:8888", "a-1", 0)
		if err := c.SetProxy("http://dc:hunter2@proxy.corp.example:8080"); err != nil {
			t.Fatal(err)
		}
		got := join(c, "windows")
		if !strings.Contains(got, "proxy.corp.example:8080") || !strings.Contains(got, "--proxy") {
			t.Errorf("the explicit proxy is not named: %q", got)
		}
		if strings.Contains(got, "hunter2") {
			t.Errorf("the proxy password was logged: %q", got)
		}
		if strings.Contains(got, systemProxy) {
			t.Errorf("the operator chose a route; the system-proxy note is noise here: %q", got)
		}
	})

	t.Run("direct", func(t *testing.T) {
		c := New("http://simcore:8888", "a-1", 0)
		if err := c.SetProxy("direct"); err != nil {
			t.Fatal(err)
		}
		if got := join(c, "windows"); !strings.Contains(got, "reached directly") || !strings.Contains(got, "--proxy direct") {
			t.Errorf("--proxy direct is not stated: %q", got)
		}
	})
}
