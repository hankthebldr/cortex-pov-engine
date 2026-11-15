package ealrelay

import (
	"bytes"
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
}

func newStandIn(t *testing.T, respond func(Request) (Response, error)) (*standIn, string) {
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
	ticket, err := srv.Issue("step-03")
	if err != nil {
		t.Fatal(err)
	}
	return s, ticket
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
			s, ticket := newStandIn(t, func(Request) (Response, error) { return tc.resp, nil })
			code, stdout, stderr := eal(t, &Runner{},
				"--relay", s.Addr(), "--ticket", ticket, "run", path, "--live")
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
	s, ticket := newStandIn(t, func(req Request) (Response, error) {
		return Response{Stdout: "{\"asked\": \"" + req.Verb + " " + req.Plugin + "\"}\n"}, nil
	})
	code, stdout, _ := eal(t, &Runner{}, "--relay", s.Addr(), "--ticket", ticket, "describe", "dns_tunnel_exfil")
	if code != 0 || stdout != "{\"asked\": \"describe dns_tunnel_exfil\"}\n" {
		t.Errorf("describe: exit=%d stdout=%q", code, stdout)
	}
	code, stdout, _ = eal(t, &Runner{}, "--relay", s.Addr(), "--ticket", ticket, "list-plugins")
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
	s, ticket := newStandIn(t, func(Request) (Response, error) {
		return Response{Stdout: "{\"status\": \"complete\"}\n", Ops: ops,
			Allowlist: []string{"c2.example.test:8080"}}, nil
	})
	fake := &fakeNet{}
	code, stdout, _ := eal(t, fake.runner(),
		"--relay", s.Addr(), "--ticket", ticket, "run", writeCampaign(t, "x: 1\n"), "--live")
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
	s, ticket := newStandIn(t, func(Request) (Response, error) {
		return Response{Ops: []Op{op}, Allowlist: []string{"elsewhere.example.test:80"}}, nil
	})
	fake := &fakeNet{}
	code, stdout, _ := eal(t, fake.runner(), "--relay", s.Addr(), "--ticket", ticket, "run", path)
	if code == 0 || !strings.Contains(stdout, "EAL_WIRE_OP_REFUSED") || len(fake.trace) != 0 {
		t.Errorf("refused plan: exit=%d trace=%v stdout=%s", code, fake.trace, stdout)
	}

	// Allowed, but no request gets an answer.
	s, ticket = newStandIn(t, func(Request) (Response, error) {
		return Response{Ops: []Op{op}, Allowlist: []string{"c2.example.test:8080"}}, nil
	})
	dead := &Runner{HTTP: func(*http.Request, Op) (*http.Response, error) {
		return nil, os.ErrDeadlineExceeded
	}}
	code, stdout, _ = eal(t, dead, "--relay", s.Addr(), "--ticket", ticket, "run", path)
	if code == 0 || !strings.Contains(stdout, "EAL_WIRE_NOT_DELIVERED: 0 of 1") {
		t.Errorf("undelivered plan: exit=%d stdout=%s", code, stdout)
	}
}

func TestEalWithoutAGoodTicketReachesNothing(t *testing.T) {
	s, _ := newStandIn(t, func(Request) (Response, error) { return Response{Stdout: "served\n"}, nil })
	path := writeCampaign(t, "x: 1\n")

	code, stdout, stderr := eal(t, &Runner{}, "--relay", s.Addr(), "--ticket", "not-a-ticket", "run", path)
	if code != exitRelay || stdout != "" || !strings.Contains(stderr, "EAL_RELAY_REFUSED: EAL_RELAY_TICKET_REFUSED") {
		t.Errorf("bad ticket: exit=%d stdout=%q stderr=%q", code, stdout, stderr)
	}
	if len(s.asked) != 0 {
		t.Errorf("a call with a bad ticket was forwarded: %+v", s.asked)
	}

	// SimCore's own refusal comes back with its code.
	s2, ticket := newStandIn(t, func(Request) (Response, error) {
		return Response{}, &Refusal{Code: "EAL_RELAY_STEP_NOT_MARKED", Detail: "no run marked this step"}
	})
	code, _, stderr = eal(t, &Runner{}, "--relay", s2.Addr(), "--ticket", ticket, "run", path)
	if code != exitRelay || !strings.Contains(stderr, "EAL_RELAY_STEP_NOT_MARKED") {
		t.Errorf("SimCore refusal: exit=%d stderr=%q", code, stderr)
	}

	// Once the task is over the listener is gone and so is the ticket.
	s2.Close()
	code, _, stderr = eal(t, &Runner{}, "--relay", s2.Addr(), "--ticket", ticket, "run", path)
	if code != exitRelay || !strings.Contains(stderr, "EAL_RELAY_UNREACHABLE") {
		t.Errorf("closed listener: exit=%d stderr=%q", code, stderr)
	}
}

func TestEalUsageErrorsMatchTheCLI(t *testing.T) {
	s, ticket := newStandIn(t, func(Request) (Response, error) { return Response{}, nil })
	base := []string{"--relay", s.Addr(), "--ticket", ticket}
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
	// Run by hand, without the beacon's flags, it explains itself.
	if code, _, stderr := eal(t, &Runner{}, "run", "x.yml"); code != exitUsage || !strings.Contains(stderr, "--relay") {
		t.Errorf("bare call: exit=%d stderr=%q", code, stderr)
	}
}
