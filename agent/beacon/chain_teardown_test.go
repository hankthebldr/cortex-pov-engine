package beacon

// Shared fixtures for the chain-teardown tests (RG-AGENT-01 / RG-AGENT-02):
// chain_teardown_unix_test.go and chain_teardown_windows_test.go.

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// chainTeardownServer answers /control with abort:true from the Nth poll on
// (abortFromPoll; 0 = never) and records the /complete body.
type chainTeardownServer struct {
	*httptest.Server
	abortFromPoll int64
	polls         atomic.Int64
	mu            sync.Mutex
	complete      string
}

func newChainTeardownServer(t *testing.T, abortFromPoll int64) *chainTeardownServer {
	t.Helper()
	s := &chainTeardownServer{abortFromPoll: abortFromPoll}
	s.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		switch {
		case strings.HasSuffix(r.URL.Path, "/control"):
			n := s.polls.Add(1)
			_ = json.NewEncoder(w).Encode(ControlState{Abort: s.abortFromPoll > 0 && n >= s.abortFromPoll, Status: "running"})
		case strings.HasSuffix(r.URL.Path, "/complete"):
			s.mu.Lock()
			s.complete = string(body)
			s.mu.Unlock()
		}
	}))
	t.Cleanup(s.Close)
	return s
}

func (s *chainTeardownServer) completed(t *testing.T) completeBody {
	t.Helper()
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.complete == "" {
		t.Fatal("the run never posted /complete")
	}
	var b completeBody
	if err := json.Unmarshal([]byte(s.complete), &b); err != nil {
		t.Fatalf("decode /complete: %v", err)
	}
	return b
}

func directIdentityClient(url string) *BeaconClient {
	c := New(url, "a-1", 0)
	c.resolveIdentity = func(string) identity.Resolution {
		return identity.Resolution{Mode: "direct", Username: "root"}
	}
	return c
}
