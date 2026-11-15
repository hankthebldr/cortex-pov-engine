package beacon

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/hankthebldr/cortexsim/agent/runtimefacts"
)

func TestEnrollSuccessCarriesBody(t *testing.T) {
	var got map[string]interface{}
	var path, method string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path, method = r.URL.Path, r.Method
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.Write([]byte(`{"status":"enrolled","agent_id":"pod-abc","remaining_uses":3}`))
	}))
	defer srv.Close()

	facts := &runtimefacts.Facts{Container: true, PodName: "p1", Tools: []string{"curl"}, Accounts: []string{}}
	id, err := Enroll(srv.URL, "cxs_secret", "host1", "linux", "p1", []string{"shell"}, []string{"python"}, facts)
	if err != nil {
		t.Fatal(err)
	}
	if id != "pod-abc" {
		t.Errorf("id = %q", id)
	}
	if method != "POST" || path != "/api/agents/enroll" {
		t.Errorf("request = %s %s", method, path)
	}
	for k, want := range map[string]string{"token": "cxs_secret", "hostname": "host1", "os": "linux", "desired_name": "p1"} {
		if got[k] != want {
			t.Errorf("body[%s] = %v, want %q", k, got[k], want)
		}
	}
	if caps, _ := got["capabilities"].([]interface{}); len(caps) != 1 || caps[0] != "shell" {
		t.Errorf("capabilities = %v", got["capabilities"])
	}
	if _, ok := got["interpreters"]; !ok {
		t.Error("interpreters missing")
	}
	rf, ok := got["runtime_facts"].(map[string]interface{})
	if !ok || rf["pod_name"] != "p1" || rf["container"] != true {
		t.Errorf("runtime_facts = %v", got["runtime_facts"])
	}
}

func TestEnrollOmitsFactsWhenNil(t *testing.T) {
	var got map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.Write([]byte(`{"agent_id":"a"}`))
	}))
	defer srv.Close()
	if _, err := Enroll(srv.URL, "t", "h", "linux", "", nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	if _, ok := got["runtime_facts"]; ok {
		t.Error("runtime_facts must be omitted when facts are nil")
	}
}

func TestEnrollServerURLNormalised(t *testing.T) {
	var paths []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		w.Write([]byte(`{"agent_id":"a"}`))
	}))
	defer srv.Close()
	for _, in := range []string{srv.URL + "/", srv.URL + "///", "  " + srv.URL + "/\n", srv.URL + "\r\n"} {
		if _, err := Enroll(in, "t", "h", "linux", "", nil, nil, nil); err != nil {
			t.Fatalf("%q: %v", in, err)
		}
	}
	for _, p := range paths {
		if p != "/api/agents/enroll" {
			t.Errorf("request path = %q, want exactly /api/agents/enroll", p)
		}
	}
	if len(paths) != 4 {
		t.Errorf("got %d requests", len(paths))
	}
}

func TestNormalizeServerURL(t *testing.T) {
	for in, want := range map[string]string{
		"http://h:8888/":       "http://h:8888",
		" http://h:8888/\n":    "http://h:8888",
		"http://h:8888":        "http://h:8888",
		"http://h:8888/base//": "http://h:8888/base",
		"":                     "",
	} {
		if got := NormalizeServerURL(in); got != want {
			t.Errorf("NormalizeServerURL(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestEnrollDeniedParsesCodeAtEitherLevel(t *testing.T) {
	for name, body := range map[string]string{
		"nested":   `{"detail":{"error":"Invalid or expired enrollment token","code":"ENROLL_DENIED","detail":"mint"}}`,
		"toplevel": `{"error":"x","code":"ENROLL_DENIED","detail":"y"}`,
		"absent":   `not json`,
	} {
		t.Run(name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(403)
				w.Write([]byte(body))
			}))
			defer srv.Close()
			_, err := Enroll(srv.URL, "cxs_supersecret", "h", "linux", "", nil, nil, nil)
			var ee *EnrollError
			if !errors.As(err, &ee) {
				t.Fatalf("err = %v, want *EnrollError", err)
			}
			if ee.Status != 403 || ee.Code != "ENROLL_DENIED" {
				t.Errorf("got %+v", ee)
			}
			if strings.Contains(err.Error(), "supersecret") {
				t.Errorf("error leaks the token: %v", err)
			}
		})
	}
}

func TestEnrollServerDownIsUnreachable(t *testing.T) {
	srv := httptest.NewServer(http.NotFoundHandler())
	url := srv.URL
	srv.Close()
	_, err := Enroll(url, "t", "h", "linux", "", nil, nil, nil)
	var ee *EnrollError
	if !errors.As(err, &ee) || ee.Code != "SERVER_UNREACHABLE" {
		t.Fatalf("err = %v, want SERVER_UNREACHABLE", err)
	}
}

func TestEnrollDesiredNameClampedToServerLimit(t *testing.T) {
	var got map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&got)
		w.Write([]byte(`{"agent_id":"a"}`))
	}))
	defer srv.Close()
	long := strings.Repeat("é", 200)
	if _, err := Enroll(srv.URL, "t", "h", "linux", long, nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	if n := len([]rune(got["desired_name"].(string))); n != 60 {
		t.Errorf("desired_name has %d chars, want 60 (server limit)", n)
	}
}

func TestEnrollRejectsEmptyAgentID(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"status":"enrolled"}`))
	}))
	defer srv.Close()
	if _, err := Enroll(srv.URL, "t", "h", "linux", "", nil, nil, nil); err == nil {
		t.Fatal("expected an error when the server returns no agent_id")
	}
}

func TestTokenSuffix(t *testing.T) {
	if got := TokenSuffix("cxs_abcdefghij"); got != "efghij" {
		t.Errorf("got %q", got)
	}
	for _, short := range []string{"abc", "abcdef", ""} {
		if got := TokenSuffix(short); got != "…" {
			t.Errorf("TokenSuffix(%q) = %q, want an ellipsis", short, got)
		}
	}
}

func TestReadTokenFile(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "token")
	os.WriteFile(p, []byte("  cxs_abc\n"), 0o600)
	if tok, err := ReadTokenFile(p); err != nil || tok != "cxs_abc" {
		t.Errorf("got %q, %v", tok, err)
	}
	os.WriteFile(p, []byte(" \n\t"), 0o600)
	if _, err := ReadTokenFile(p); !errors.Is(err, ErrEnrollTokenEmpty) {
		t.Errorf("empty file err = %v, want ErrEnrollTokenEmpty", err)
	}
	if _, err := ReadTokenFile(filepath.Join(dir, "missing")); err == nil || errors.Is(err, ErrEnrollTokenEmpty) {
		t.Errorf("missing file err = %v", err)
	}
}

// The agent-id store tests that read or set POSIX permission bits (0600 file,
// 0700 dir, an unwritable dir, an unreadable file) live in enroll_unix_test.go
// (CI-04).

func TestLoadOrEnrollReusesExistingIDWithoutEnrolling(t *testing.T) {
	state := t.TempDir()
	os.WriteFile(filepath.Join(state, "agent-id"), []byte("pod-keep\n"), 0o600)
	id, reused, err := LoadOrEnroll(state, func() (string, error) {
		t.Fatal("enroll must not be called when an agent-id is already stored")
		return "", nil
	})
	if err != nil || id != "pod-keep" || !reused {
		t.Fatalf("got %q reused=%v err=%v", id, reused, err)
	}
}

func TestLoadOrEnrollWhitespaceFileEnrollsAgain(t *testing.T) {
	state := t.TempDir()
	os.WriteFile(filepath.Join(state, "agent-id"), []byte("  \n"), 0o600)
	calls := 0
	id, reused, err := LoadOrEnroll(state, func() (string, error) { calls++; return "fresh", nil })
	if err != nil || id != "fresh" || reused || calls != 1 {
		t.Fatalf("got %q reused=%v calls=%d err=%v", id, reused, calls, err)
	}
	b, _ := os.ReadFile(filepath.Join(state, "agent-id"))
	if strings.TrimSpace(string(b)) != "fresh" {
		t.Errorf("file = %q", b)
	}
}

func TestLoadOrEnrollLeavesNoProbeOrTempFiles(t *testing.T) {
	state := t.TempDir()
	if _, _, err := LoadOrEnroll(state, func() (string, error) { return "x", nil }); err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(state)
	if len(entries) != 1 || entries[0].Name() != "agent-id" {
		t.Errorf("state dir = %v, want only agent-id", entries)
	}
}

func TestEnrollDoesNotFollowRedirects(t *testing.T) {
	other := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("redirect target received a request (%s %s): the token would have leaked", r.Method, r.URL.Path)
	}))
	defer other.Close()
	for _, code := range []int{307, 308} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			http.Redirect(w, r, other.URL+"/api/agents/enroll", code)
		}))
		_, err := Enroll(srv.URL, "cxs_secret", "h", "linux", "", nil, nil, nil)
		srv.Close()
		var ee *EnrollError
		if !errors.As(err, &ee) || ee.Status != code {
			t.Errorf("status %d: err = %v, want *EnrollError with that status", code, err)
		}
	}
}

func TestLoadOrEnrollPropagatesEnrollError(t *testing.T) {
	state := t.TempDir()
	want := &EnrollError{Status: 403, Code: "ENROLL_DENIED"}
	_, _, err := LoadOrEnroll(state, func() (string, error) { return "", want })
	if !errors.Is(err, want) {
		t.Fatalf("err = %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(state, "agent-id")); statErr == nil {
		t.Error("no agent-id may be written when enrollment failed")
	}
}

func TestEnrollWithRetryOnlyRetriesUnreachable(t *testing.T) {
	var slept []time.Duration
	sleep := func(d time.Duration) { slept = append(slept, d) }

	calls := 0
	id, err := EnrollWithRetry(5, 3*time.Second, sleep, func() (string, error) {
		calls++
		if calls < 3 {
			return "", &EnrollError{Code: "SERVER_UNREACHABLE"}
		}
		return "ok", nil
	})
	if err != nil || id != "ok" || calls != 3 || len(slept) != 2 || slept[0] != 3*time.Second {
		t.Fatalf("id=%q err=%v calls=%d slept=%v", id, err, calls, slept)
	}

	calls, slept = 0, nil
	_, err = EnrollWithRetry(5, time.Second, sleep, func() (string, error) {
		calls++
		return "", &EnrollError{Status: 403, Code: "ENROLL_DENIED"}
	})
	if calls != 1 || err == nil {
		t.Errorf("denied must not be retried: calls=%d err=%v", calls, err)
	}

	calls = 0
	_, err = EnrollWithRetry(5, time.Second, sleep, func() (string, error) {
		calls++
		return "", &EnrollError{Code: "SERVER_UNREACHABLE"}
	})
	var ee *EnrollError
	if calls != 5 || !errors.As(err, &ee) || ee.Code != "SERVER_UNREACHABLE" {
		t.Errorf("calls=%d err=%v", calls, err)
	}
}

func TestRegisterIncludesRuntimeFactsWhenSet(t *testing.T) {
	var bodies []map[string]interface{}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var b map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&b)
		bodies = append(bodies, b)
		w.Write([]byte(`{}`))
	}))
	defer srv.Close()

	c := New(srv.URL, "a1", time.Second)
	if err := c.Register("h", "linux", []string{"shell"}, nil); err != nil {
		t.Fatal(err)
	}
	if _, ok := bodies[0]["runtime_facts"]; ok {
		t.Error("runtime_facts must be absent when unset")
	}

	c.SetRuntimeFacts(&runtimefacts.Facts{Container: true, PodName: "p", Tools: []string{}, Accounts: []string{}})
	if err := c.Register("h", "linux", []string{"shell"}, nil); err != nil {
		t.Fatal(err)
	}
	rf, ok := bodies[1]["runtime_facts"].(map[string]interface{})
	if !ok || rf["pod_name"] != "p" {
		t.Errorf("runtime_facts = %v", bodies[1]["runtime_facts"])
	}
	// reRegister replays them too.
	if err := c.reRegister(); err != nil {
		t.Fatal(err)
	}
	if _, ok := bodies[2]["runtime_facts"]; !ok {
		t.Error("reRegister dropped runtime_facts")
	}
}

func TestWriteIDFileFailureLeavesNothingBehind(t *testing.T) {
	old := writeIDData
	writeIDData = func(*os.File, string) error { return errors.New("disk full") }
	defer func() { writeIDData = old }()

	state := t.TempDir()
	if err := writeIDFile(state, "pod-1"); err == nil {
		t.Fatal("writeIDFile must return the write error")
	}
	if entries, _ := os.ReadDir(state); len(entries) != 0 {
		t.Errorf("state dir must be empty after a failed write, has %v", entries)
	}

	// A pre-existing good id must survive a failed rewrite.
	os.WriteFile(filepath.Join(state, "agent-id"), []byte("old\n"), 0o600)
	if err := writeIDFile(state, "pod-2"); err == nil {
		t.Fatal("expected error")
	}
	if b, _ := os.ReadFile(filepath.Join(state, "agent-id")); string(b) != "old\n" {
		t.Errorf("existing agent-id was clobbered: %q", b)
	}
}

func TestLoadOrEnrollSurfacesWriteFailureAsStateDirError(t *testing.T) {
	old := writeIDData
	writeIDData = func(*os.File, string) error { return errors.New("disk full") }
	defer func() { writeIDData = old }()

	state := t.TempDir()
	_, _, err := LoadOrEnroll(state, func() (string, error) { return "pod-1", nil })
	var se *StateDirError
	if !errors.As(err, &se) {
		t.Fatalf("err = %v, want *StateDirError", err)
	}
	if entries, _ := os.ReadDir(state); len(entries) != 0 {
		t.Errorf("no agent-id or temp file may remain, got %v", entries)
	}
}

// A pod with no detectable interpreter sent "interpreters": null (a nil Go
// slice) and SimCore answered 422 (kind run, D2). Both lists go on the wire as
// [] on Enroll and on Register, asserted on the raw JSON.
func TestEnrollAndRegisterSendEmptyListsNotNull(t *testing.T) {
	var raws []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		raws = append(raws, string(b))
		w.Write([]byte(`{"agent_id":"a"}`))
	}))
	defer srv.Close()

	if _, err := Enroll(srv.URL, "cxs_x", "h", "linux", "p", nil, nil, nil); err != nil {
		t.Fatal(err)
	}
	c := New(srv.URL, "a1", time.Second)
	if err := c.Register("h", "linux", nil, nil); err != nil {
		t.Fatal(err)
	}
	if err := c.reRegister(); err != nil {
		t.Fatal(err)
	}
	if len(raws) != 3 {
		t.Fatalf("requests = %d", len(raws))
	}
	for i, raw := range raws {
		wants := []string{`"capabilities":[]`, `"interpreters":[]`}
		if i == 2 { // reRegister substitutes its default capabilities for an empty list
			wants = wants[1:]
		}
		for _, want := range wants {
			if !strings.Contains(raw, want) {
				t.Errorf("request %d: %s missing from %s", i, want, raw)
			}
		}
		if strings.Contains(raw, "null") {
			t.Errorf("request %d carries a null: %s", i, raw)
		}
	}
}
