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

const (
	// maxRequestBytes bounds what the subcommand may send the beacon. SimCore
	// refuses a campaign over 256 KiB; this only keeps a stray writer out.
	maxRequestBytes = 2 << 20
	// maxTicketUses is how many calls one step may make (a describe and a run,
	// a handful of campaigns). SimCore keeps its own, lower, count.
	maxTicketUses = 32
)

// Server is the beacon's end of the relay: a loopback listener that exists
// only while a task with marked steps runs. A ticket names one of those steps;
// the step's own subcommand presents it, and the beacon forwards the call to
// SimCore under the run and step it issued the ticket for. The caller chooses
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
	uses   int
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

// Issue returns a new ticket for stepID.
func (s *Server) Issue(stepID string) (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	t := hex.EncodeToString(raw[:])
	s.mu.Lock()
	s.tickets[t] = &ticket{stepID: stepID}
	s.mu.Unlock()
	return t, nil
}

// Close stops the listener. Every ticket dies with it.
func (s *Server) Close() {
	s.mu.Lock()
	s.tickets = map[string]*ticket{}
	s.mu.Unlock()
	_ = s.srv.Close()
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
	s.mu.Lock()
	t := s.tickets[req.Ticket]
	ok := t != nil && t.uses < maxTicketUses
	var stepID string
	if ok {
		t.uses++
		stepID = t.stepID
	}
	s.mu.Unlock()
	if !ok {
		refuse(w, http.StatusForbidden, "EAL_RELAY_TICKET_REFUSED",
			"this ticket is not good for a step of the task that is running")
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
