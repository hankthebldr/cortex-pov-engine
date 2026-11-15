package ealrelay

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"sync"
	"time"
)

// Request is one EAL CLI call, as the subcommand hands it to the beacon.
type Request struct {
	Ticket       string `json:"ticket,omitempty"`
	Verb         string `json:"verb"` // run | describe | list-plugins
	CampaignYAML string `json:"campaign_yaml,omitempty"`
	Plugin       string `json:"plugin,omitempty"`
	Live         bool   `json:"live,omitempty"`
}

// Response is SimCore's answer to POST /api/eal/relay.
type Response struct {
	ExitCode  int      `json:"exit_code"`
	Stdout    string   `json:"stdout"`
	Stderr    string   `json:"stderr"`
	Ops       []Op     `json:"ops"`
	Allowlist []string `json:"allowlist"`
}

// Refusal is a request that was not served: by the beacon (a bad ticket) or by
// SimCore (the step is not marked, the run has ended). Code is stable.
type Refusal struct {
	Code   string `json:"code"`
	Detail string `json:"detail"`
}

func (r *Refusal) Error() string { return r.Code + ": " + r.Detail }

// Forward sends one request to SimCore for the step a ticket belongs to.
type Forward func(stepID string, req Request) (Response, error)

// maxRequestBytes bounds what the subcommand may send the beacon. SimCore
// refuses a campaign over 256 KiB; this only keeps a stray writer out.
const maxRequestBytes = 2 << 20

// Server is the beacon's end of the relay: a listener on loopback, and only
// loopback, that exists while a task with marked steps runs.
//
// A step may ask only while it is running, and only as often as its command
// calls the EAL CLI. Open is called when the step starts and returns its
// ticket; Revoke is called when the step ends. The beacon forwards a call to
// SimCore under the run and step it opened the ticket for. The caller chooses
// neither.
type Server struct {
	ln      net.Listener
	srv     *http.Server
	forward Forward

	mu      sync.Mutex
	tickets map[string]*ticket
}

type ticket struct {
	stepID string
	left   int // calls the step may still make
}

// Listen starts the listener on a free loopback port.
func Listen(forward Forward) (*Server, error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	s := &Server{ln: ln, forward: forward, tickets: map[string]*ticket{}}
	mux := http.NewServeMux()
	mux.HandleFunc("/relay", s.handle)
	s.srv = &http.Server{Handler: mux, ReadHeaderTimeout: 10 * time.Second}
	go func() { _ = s.srv.Serve(ln) }()
	return s, nil
}

// Addr is the host:port the subcommand connects to.
func (s *Server) Addr() string { return s.ln.Addr().String() }

// Open lets stepID make up to calls requests and returns the ticket that
// proves a request is the step's. Call it when the step starts.
func (s *Server) Open(stepID string, calls int) (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	t := hex.EncodeToString(raw[:])
	s.mu.Lock()
	s.tickets[t] = &ticket{stepID: stepID, left: calls}
	s.mu.Unlock()
	return t, nil
}

// Revoke ends a ticket. Call it when the step ends: a request that presents
// the ticket afterwards is refused.
func (s *Server) Revoke(t string) {
	s.mu.Lock()
	delete(s.tickets, t)
	s.mu.Unlock()
}

// Close stops the listener. Every ticket dies with it.
func (s *Server) Close() {
	s.mu.Lock()
	s.tickets = map[string]*ticket{}
	s.mu.Unlock()
	_ = s.srv.Close()
}

// claim spends one of a ticket's calls and returns the step it belongs to.
func (s *Server) claim(t string) (stepID string, refusal string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	tk := s.tickets[t]
	switch {
	case tk == nil:
		return "", "this ticket is not for a step that is running"
	case tk.left <= 0:
		return "", "this step has already made every EAL CLI call its command contains"
	}
	tk.left--
	return tk.stepID, ""
}

func (s *Server) handle(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		refuse(w, http.StatusMethodNotAllowed, "EAL_RELAY_BAD_REQUEST", "POST only")
		return
	}
	var req Request
	if err := json.NewDecoder(io.LimitReader(r.Body, maxRequestBytes)).Decode(&req); err != nil {
		refuse(w, http.StatusBadRequest, "EAL_RELAY_BAD_REQUEST", "the request is not JSON")
		return
	}
	stepID, refusal := s.claim(req.Ticket)
	if refusal != "" {
		refuse(w, http.StatusForbidden, "EAL_RELAY_TICKET_REFUSED", refusal)
		return
	}
	req.Ticket = ""
	resp, err := s.forward(stepID, req)
	if err != nil {
		var ref *Refusal
		if errors.As(err, &ref) {
			refuse(w, http.StatusForbidden, ref.Code, ref.Detail)
			return
		}
		refuse(w, http.StatusBadGateway, "EAL_RELAY_UNREACHABLE", err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

func refuse(w http.ResponseWriter, status int, code, detail string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(Refusal{Code: code, Detail: detail})
}

// ParseRefusal reads a refusal out of an error body: the beacon's own
// {code, detail}, or SimCore's {"detail": {error, code, detail}}.
func ParseRefusal(status int, body []byte) *Refusal {
	var flat Refusal
	if json.Unmarshal(body, &flat) == nil && flat.Code != "" {
		return &flat
	}
	var wrapped struct {
		Detail Refusal `json:"detail"`
	}
	if json.Unmarshal(body, &wrapped) == nil && wrapped.Detail.Code != "" {
		return &wrapped.Detail
	}
	return &Refusal{Code: "EAL_RELAY_UNREACHABLE", Detail: fmt.Sprintf("status %d", status)}
}
