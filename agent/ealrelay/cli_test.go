package ealrelay

import (
	"bytes"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

// standIn is the beacon's listener with SimCore replaced by a function, so the
// subcommand is exercised end to end over a real loopback connection.
type standIn struct {
	*Server
	mu      sync.Mutex
	steps   []string
	asked   []Request
	respond func(Request) (Response, error)
	ticket  string // the open ticket, as the beacon would write it to a file
}

// newStandIn opens a ticket for step-03 good for calls requests and returns
// the file it is in.
func newStandIn(t *testing.T, calls int, respond func(Request) (Response, error)) (*standIn, string) {
	t.Helper()
	s := &standIn{respond: respond}
	srv, err := Listen(func(stepID string, req Request) (Response, error) {
		s.mu.Lock()
		s.steps = append(s.steps, stepID)
		s.asked = append(s.asked, req)
		s.mu.Unlock()
		return s.respond(req)
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(srv.Close)
	s.Server = srv
	if s.ticket, err = srv.Open("step-03", calls); err != nil {
		t.Fatal(err)
	}
	file := filepath.Join(t.TempDir(), "ticket")
	if err := os.WriteFile(file, []byte(s.ticket+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return s, file
}

func eal(t *testing.T, runner *Runner, args ...string) (code int, stdout, stderr string) {
	t.Helper()
	var out, errb bytes.Buffer
	code = run(args, &out, &errb, runner, &http.Transport{})
	return code, out.String(), errb.String()
}

func writeCampaign(t *testing.T, text string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "campaign.yml")
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestEalRunPrintsServerOutputAndExitCode(t *testing.T) {
	const campaign = "campaign_id: CMP-NDR-001\nsteps: []\n"
	path := writeCampaign(t, campaign)

	cases := []struct {
		name string
		resp Response
	}{
		{"a campaign SimCore ran", Response{ExitCode: 0, Stdout: "{\n  \"status\": \"complete\"\n}\n"}},
		{"a campaign that did not complete", Response{ExitCode: 1, Stdout: "{\n  \"status\": \"aborted\"\n}\n"}},
		{"a plugin that cannot be relayed", Response{ExitCode: 3, Stdout: "EAL_WIRE_NOT_RELAYABLE: llm_provider_egress\n"}},
		{"a usage error", Response{ExitCode: 2, Stderr: "error: invalid campaign spec: x\n"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, ticketFile := newStandIn(t, 1, func(Request) (Response, error) { return tc.resp, nil })
			code, stdout, stderr := eal(t, &Runner{},
				"--relay", s.Addr(), "--ticket-file", ticketFile, "run", path, "--live")
			if code != tc.resp.ExitCode || stdout != tc.resp.Stdout || stderr != tc.resp.Stderr {
				t.Errorf("got exit=%d stdout=%q stderr=%q, want %+v", code, stdout, stderr, tc.resp)
			}
			// The beacon forwarded the file's text for the step the ticket names;
			// the ticket itself goes no further than the beacon.
			if len(s.asked) != 1 || s.steps[0] != "step-03" {
				t.Fatalf("forwarded %d call(s) for %v", len(s.asked), s.steps)
			}
			want := Request{Verb: "run", CampaignYAML: campaign, Live: true}
			if s.asked[0] != want {
				t.Errorf("forwarded %+v, want %+v", s.asked[0], want)
			}
		})
	}
}

func TestEalDescribeAndListPluginsPassThrough(t *testing.T) {
	s, ticketFile := newStandIn(t, 2, func(req Request) (Response, error) {
		return Response{Stdout: "{\"asked\": \"" + req.Verb + " " + req.Plugin + "\"}\n"}, nil
	})
	code, stdout, _ := eal(t, &Runner{}, "--relay", s.Addr(), "--ticket-file", ticketFile, "describe", "dns_tunnel_exfil")
	if code != 0 || stdout != "{\"asked\": \"describe dns_tunnel_exfil\"}\n" {
		t.Errorf("describe: exit=%d stdout=%q", code, stdout)
	}
	code, stdout, _ = eal(t, &Runner{}, "--relay", s.Addr(), "--ticket-file", ticketFile, "list-plugins")
	if code != 0 || stdout != "{\"asked\": \"list-plugins \"}\n" {
		t.Errorf("list-plugins: exit=%d stdout=%q", code, stdout)
	}
}

func TestEalPerformsReturnedOperationsAndReportsThem(t *testing.T) {
	ops := []Op{
		{Op: "http", Method: "GET", URL: "http://c2.example.test:8080/checkin?q=a"},
		{Op: "sleep", Seconds: 2},
		{Op: "http", Method: "GET", URL: "http://c2.example.test:8080/checkin?q=b"},
	}
	s, ticketFile := newStandIn(t, 1, func(Request) (Response, error) {
		return Response{Stdout: "{\"status\": \"complete\"}\n", Ops: ops,
			Allowlist: []string{"c2.example.test:8080"}}, nil
	})
	fake := &fakeNet{}
	code, stdout, _ := eal(t, fake.runner(),
		"--relay", s.Addr(), "--ticket-file", ticketFile, "run", writeCampaign(t, "x: 1\n"), "--live")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, stdout)
	}
	if len(fake.trace) != 3 || !strings.HasPrefix(fake.trace[1], "sleep 2s") {
		t.Errorf("operations not performed in order: %v", fake.trace)
	}
	if !strings.HasPrefix(stdout, "{\"status\": \"complete\"}\n") ||
		!strings.Contains(stdout, `"network_operations": 2`) || !strings.Contains(stdout, `"reached": 2`) {
		t.Errorf("stdout does not carry SimCore's output then the account of what was sent:\n%s", stdout)
	}
}

func TestEalExitsNonZeroWhenThePlanIsRefusedOrNothingArrives(t *testing.T) {
	path := writeCampaign(t, "x: 1\n")
	op := Op{Op: "http", Method: "GET", URL: "http://c2.example.test:8080/"}

	// Outside the allowlist: refused whole, nothing sent.
	s, ticketFile := newStandIn(t, 1, func(Request) (Response, error) {
		return Response{Ops: []Op{op}, Allowlist: []string{"elsewhere.example.test:80"}}, nil
	})
	fake := &fakeNet{}
	code, stdout, _ := eal(t, fake.runner(), "--relay", s.Addr(), "--ticket-file", ticketFile, "run", path)
	if code == 0 || !strings.Contains(stdout, "EAL_WIRE_OP_REFUSED") || len(fake.trace) != 0 {
		t.Errorf("refused plan: exit=%d trace=%v stdout=%s", code, fake.trace, stdout)
	}

	// Allowed, but no request gets an answer.
	s, ticketFile = newStandIn(t, 1, func(Request) (Response, error) {
		return Response{Ops: []Op{op}, Allowlist: []string{"c2.example.test:8080"}}, nil
	})
	dead := &Runner{HTTP: func(*http.Request, Op) (*http.Response, error) {
		return nil, os.ErrDeadlineExceeded
	}}
	code, stdout, _ = eal(t, dead, "--relay", s.Addr(), "--ticket-file", ticketFile, "run", path)
	if code == 0 || !strings.Contains(stdout, "EAL_WIRE_NOT_DELIVERED: 0 of 1") {
		t.Errorf("undelivered plan: exit=%d stdout=%s", code, stdout)
	}
}

// The beacon revokes a step's ticket the moment the step ends. Whatever still
// holds it (a process the step left behind, a copy somebody took) gets nothing.
func TestTicketRefusedAfterStepEnds(t *testing.T) {
	s, ticketFile := newStandIn(t, 5, func(Request) (Response, error) { return Response{Stdout: "served\n"}, nil })
	path := writeCampaign(t, "x: 1\n")
	args := []string{"--relay", s.Addr(), "--ticket-file", ticketFile, "run", path}

	if code, stdout, _ := eal(t, &Runner{}, args...); code != 0 || stdout != "served\n" {
		t.Fatalf("while the step runs: exit=%d stdout=%q", code, stdout)
	}

	s.Revoke(s.ticket) // the step ends

	code, stdout, stderr := eal(t, &Runner{}, args...)
	if code != exitRelay || stdout != "" || !strings.Contains(stderr, "EAL_RELAY_REFUSED: EAL_RELAY_TICKET_REFUSED") {
		t.Errorf("after the step ended: exit=%d stdout=%q stderr=%q", code, stdout, stderr)
	}
	if len(s.asked) != 1 {
		t.Errorf("SimCore was asked %d times, want only the call made while the step ran", len(s.asked))
	}

	// The beacon also removes the file. The subcommand then has nothing to send.
	if err := os.Remove(ticketFile); err != nil {
		t.Fatal(err)
	}
	code, _, stderr = eal(t, &Runner{}, args...)
	if code != exitRelay || !strings.Contains(stderr, "EAL_RELAY_TICKET_UNREADABLE") {
		t.Errorf("with the file gone: exit=%d stderr=%q", code, stderr)
	}
}

// A step may ask as often as its command calls the EAL CLI, and no more.
func TestTicketRefusedBeyondDeclaredCalls(t *testing.T) {
	// The beacon counts the calls in the step's command; this one has two.
	const command = "python3 -m scripts.eal_simulator.cli describe dns_tunnel_exfil | jq .\n" +
		"python3 -m scripts.eal_simulator.cli run /tmp/c.yml --live\n"
	calls := CountCalls(command)
	if calls != 2 {
		t.Fatalf("CountCalls = %d, want 2", calls)
	}
	s, ticketFile := newStandIn(t, calls, func(Request) (Response, error) { return Response{Stdout: "served\n"}, nil })
	args := []string{"--relay", s.Addr(), "--ticket-file", ticketFile, "list-plugins"}

	for i := 1; i <= calls; i++ {
		if code, stdout, _ := eal(t, &Runner{}, args...); code != 0 || stdout != "served\n" {
			t.Fatalf("call %d of %d: exit=%d stdout=%q", i, calls, code, stdout)
		}
	}
	code, stdout, stderr := eal(t, &Runner{}, args...)
	if code != exitRelay || stdout != "" || !strings.Contains(stderr, "EAL_RELAY_TICKET_REFUSED") {
		t.Errorf("call %d: exit=%d stdout=%q stderr=%q", calls+1, code, stdout, stderr)
	}
	if len(s.asked) != calls {
		t.Errorf("SimCore was asked %d times, want %d", len(s.asked), calls)
	}
}

func TestEalWithoutAGoodTicketReachesNothing(t *testing.T) {
	s, _ := newStandIn(t, 1, func(Request) (Response, error) { return Response{Stdout: "served\n"}, nil })
	path := writeCampaign(t, "x: 1\n")

	// A ticket the beacon never issued.
	forged := filepath.Join(t.TempDir(), "forged")
	if err := os.WriteFile(forged, []byte("not-a-ticket\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	code, stdout, stderr := eal(t, &Runner{}, "--relay", s.Addr(), "--ticket-file", forged, "run", path)
	if code != exitRelay || stdout != "" || !strings.Contains(stderr, "EAL_RELAY_REFUSED: EAL_RELAY_TICKET_REFUSED") {
		t.Errorf("forged ticket: exit=%d stdout=%q stderr=%q", code, stdout, stderr)
	}
	if len(s.asked) != 0 {
		t.Errorf("a call with a forged ticket was forwarded: %+v", s.asked)
	}

	// SimCore's own refusal comes back with its code.
	s2, ticketFile := newStandIn(t, 1, func(Request) (Response, error) {
		return Response{}, &Refusal{Code: "EAL_RELAY_STEP_NOT_MARKED", Detail: "no run marked this step"}
	})
	code, _, stderr = eal(t, &Runner{}, "--relay", s2.Addr(), "--ticket-file", ticketFile, "run", path)
	if code != exitRelay || !strings.Contains(stderr, "EAL_RELAY_STEP_NOT_MARKED") {
		t.Errorf("SimCore refusal: exit=%d stderr=%q", code, stderr)
	}

	// Once the task is over the listener is gone.
	s2.Close()
	code, _, stderr = eal(t, &Runner{}, "--relay", s2.Addr(), "--ticket-file", ticketFile, "run", path)
	if code != exitRelay || !strings.Contains(stderr, "EAL_RELAY_UNREACHABLE") {
		t.Errorf("closed listener: exit=%d stderr=%q", code, stderr)
	}
}

// The listener is for processes on this host only.
func TestRelayListensOnLoopbackOnly(t *testing.T) {
	s, _ := newStandIn(t, 1, func(Request) (Response, error) { return Response{}, nil })
	host, _, err := net.SplitHostPort(s.Addr())
	if err != nil {
		t.Fatal(err)
	}
	if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
		t.Errorf("the relay listens on %s, which is not a loopback address", s.Addr())
	}
}

func TestEalUsageErrorsMatchTheCLI(t *testing.T) {
	s, ticketFile := newStandIn(t, 9, func(Request) (Response, error) { return Response{}, nil })
	base := []string{"--relay", s.Addr(), "--ticket-file", ticketFile}
	for _, args := range [][]string{
		{"run"},
		{"run", "/no/such/campaign.yml", "--live"},
		{"describe"},
		{"frobnicate"},
		{},
	} {
		code, _, stderr := eal(t, &Runner{}, append(append([]string{}, base...), args...)...)
		if code != exitUsage || !strings.HasPrefix(stderr, "error: ") {
			t.Errorf("%v: exit=%d stderr=%q", args, code, stderr)
		}
	}
	if len(s.asked) != 0 {
		t.Errorf("a usage error was forwarded: %+v", s.asked)
	}
	// Run by hand, without the beacon's flags, it explains itself. The old
	// form, a ticket on the command line, is not accepted either.
	if code, _, stderr := eal(t, &Runner{}, "run", "x.yml"); code != exitUsage || !strings.Contains(stderr, "--ticket-file") {
		t.Errorf("bare call: exit=%d stderr=%q", code, stderr)
	}
	if code, _, _ := eal(t, &Runner{}, "--relay", s.Addr(), "--ticket", s.ticket, "list-plugins"); code != exitUsage {
		t.Errorf("a ticket passed as an argument was accepted: exit=%d", code)
	}
}
