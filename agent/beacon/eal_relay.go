package beacon

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"github.com/hankthebldr/cortexsim/agent/ealrelay"
)

// The beacon's half of the EAL relay (see agent/ealrelay). SimCore marks a step
// `eal_relay` when its command calls the SimCore-only EAL CLI. Before the task
// runs, the beacon opens a loopback listener. As a marked step is about to be
// executed, and only in the text handed to the executor, that one call is
// swapped for the beacon's own `eal` subcommand: the step itself keeps the
// command its author wrote, which is what the live runtime log reports. While
// a marked step runs, and only then, the step holds a ticket; the beacon
// forwards what the subcommand asks to SimCore under this task's run and that
// step's id.
//
// Only a step's command is ever swapped. Cleanup commands (cleanup.go) do not
// pass through here: they are not steps, are never rewritten and never hold a
// ticket.
//
// The ticket is never in the step's argv or environment. It is in a file:
//
//   - in a directory the beacon makes for the task, with a random name, that
//     no other account can list or write;
//   - with a random name of its own, mode 0600, owned by the account the step
//     runs as (the beacon's own for a `direct` step). On Windows, where a step
//     always runs as the beacon, it is created with an ACL for that account;
//   - written when the step starts and removed when it ends.
//
// The command carries the file's path. The path is not a secret: reading the
// file takes the step's account.

// ealSelfPath is this beacon's own binary: what a marked step runs instead of
// python. A var so a test can stand a binary in.
var ealSelfPath = os.Executable

// ealRelayTimeout bounds one forwarded call. SimCore runs a collector campaign
// inside the request, so this is far longer than the 30 s API client allows.
const ealRelayTimeout = 10 * time.Minute

// ticketOwner is the account a ticket file is handed to: the one the step runs
// as, when that is not the beacon's own.
type ticketOwner struct {
	name     string
	uid, gid int
}

// stepRelay is one marked step of the running task.
type stepRelay struct {
	file  string       // where its ticket is while it runs
	owner *ticketOwner // nil: the beacon's own account
	// calls is how many EAL CLI calls the text handed to the executor has: the
	// number of requests its ticket is good for. Set by ealRelayCommand, on the
	// task's goroutine, before ealRelayStep reads it.
	calls int
}

// taskRelay is the relay state of one running task.
type taskRelay struct {
	srv   *ealrelay.Server
	exe   string
	dir   string
	steps map[string]*stepRelay
}

// relayTasks holds the running task's relay state by run id, so the places
// that hand a step's command to the executor can swap its EAL call and open
// and close its ticket without the state being threaded through every
// execution path.
var relayTasks sync.Map // run id -> *taskRelay

// prepareEalRelay opens the relay for a task that has marked steps and returns
// the teardown to defer. It does not touch the task: a step's command stays as
// SimCore sent it, and is swapped only on its way to the executor
// (ealRelayCommand). A task with no marked step opens nothing.
//
// If the relay cannot be set up, the commands run as SimCore sent them: the
// step then fails as it always did on a target, loudly, instead of the beacon
// guessing at another route.
func (c *BeaconClient) prepareEalRelay(task *Task) func() {
	type planned struct {
		step  *Step
		calls int
		owner *ticketOwner
	}
	var steps []planned
	shared := false
	for i := range task.Steps {
		step := &task.Steps[i]
		if !step.EalRelay {
			continue
		}
		// Look at the text this host will actually run.
		plan := planStep(step, beaconGOOS)
		calls := ealrelay.CountCalls(plan.command)
		if plan.refuse != "" || calls == 0 {
			continue
		}
		owner := c.ealStepOwner(task, step)
		shared = shared || owner != nil
		steps = append(steps, planned{step, calls, owner})
	}
	if len(steps) == 0 {
		return func() {}
	}

	exe, err := ealSelfPath()
	var dir string
	if err == nil {
		dir, err = newTicketDir(shared)
	}
	var srv *ealrelay.Server
	if err == nil {
		srv, err = ealrelay.Listen(func(stepID string, req ealrelay.Request) (ealrelay.Response, error) {
			return c.forwardEalRelay(task.RunID, stepID, req)
		})
	}
	if err != nil {
		if dir != "" {
			_ = os.RemoveAll(dir)
		}
		log.Printf("[beacon] EAL relay could not start (run_id=%s): %v — marked steps run their command as sent", task.RunID, err)
		return func() {}
	}

	tr := &taskRelay{srv: srv, exe: exe, dir: dir, steps: map[string]*stepRelay{}}
	for _, p := range steps {
		name, err := randomName()
		if err != nil {
			log.Printf("[beacon] EAL relay: step %s left as sent: %v", p.step.ID, err)
			continue
		}
		tr.steps[p.step.ID] = &stepRelay{file: filepath.Join(dir, name), owner: p.owner}
		log.Printf("[beacon] EAL relay: step %s — %d EAL CLI call(s) go through this beacon", p.step.ID, p.calls)
	}
	relayTasks.Store(task.RunID, tr)
	return func() {
		relayTasks.Delete(task.RunID)
		srv.Close()
		_ = os.RemoveAll(dir)
	}
}

// ealRelayCommand returns the text to execute for a step: command, with each
// EAL CLI call swapped for this beacon's `eal` subcommand when the step is one
// the task marked. For any other step it returns command unchanged.
//
// It is called where a step's command is handed to the executor, after the
// per-OS variant is chosen and before the identity harness wraps it. The step
// keeps its own text, so what is reported as the command that was attempted
// (the live `start` frame) is what the scenario's author wrote, not a path
// into this beacon's ticket directory.
func ealRelayCommand(runID, stepID, command, shell string) string {
	v, ok := relayTasks.Load(runID)
	if !ok {
		return command
	}
	tr := v.(*taskRelay)
	st := tr.steps[stepID]
	if st == nil {
		return command
	}
	inv := ealrelay.Invocation{Exe: tr.exe, Relay: tr.srv.Addr(), TicketFile: st.file}
	command, st.calls = ealrelay.Rewrite(command, beaconGOOS, shell, inv)
	return command
}

// ealRelayStep opens a marked step's ticket and returns what closes it. It is
// deferred by the two functions that run a step's command (runStep and
// runChainedStep), so the ticket exists exactly while that command runs: it is
// made and written as the step starts, and revoked and its file removed as the
// step ends. For any other step, and for a marked step whose text had no call
// to swap, it does nothing.
func ealRelayStep(runID, stepID string) func() {
	v, ok := relayTasks.Load(runID)
	if !ok {
		return func() {}
	}
	tr := v.(*taskRelay)
	st := tr.steps[stepID]
	if st == nil || st.calls == 0 {
		return func() {}
	}
	ticket, err := tr.srv.Open(stepID, st.calls)
	if err == nil {
		if err = writeTicketFile(st.file, ticket, st.owner); err != nil {
			tr.srv.Revoke(ticket)
		}
	}
	if err != nil {
		// The step still runs. Its EAL call will say the ticket is unreadable.
		log.Printf("[beacon] EAL relay: no ticket for step %s: %v", stepID, err)
		return func() {}
	}
	return func() {
		tr.srv.Revoke(ticket)
		_ = os.Remove(st.file)
	}
}

// ealStepOwner is the account the step will run as when that is not the
// beacon's own: the one its ticket file must belong to. It resolves the step's
// identity exactly as the execution paths do.
func (c *BeaconClient) ealStepOwner(task *Task, step *Step) *ticketOwner {
	if beaconGOOS == "windows" {
		return nil // a Windows step always runs as the beacon
	}
	res := c.resolve(c.resolveStepUsername(task, step))
	if res.Mode == "" || res.Mode == "direct" || res.Username == "" {
		return nil
	}
	uid, gid, err := ealAccountIDs(res.Username)
	if err != nil {
		log.Printf("[beacon] EAL relay: step %s runs as %q but its uid is unknown (%v); its ticket stays the beacon's", step.ID, res.Username, err)
		return nil
	}
	return &ticketOwner{name: res.Username, uid: uid, gid: gid}
}

// ealAccountIDs looks an account's uid and gid up. A var so a test can answer
// for an account the test host does not have.
var ealAccountIDs = lookupAccountIDs

func randomName() (string, error) {
	var raw [16]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw[:]), nil
}

// ealRelayClient is the HTTP client for forwarded calls: the beacon's own
// route to SimCore (--proxy included), no client-wide timeout, and no
// redirects, which would carry a campaign to a host nobody named.
func (c *BeaconClient) ealRelayClient() *http.Client {
	client := &http.Client{
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	if c.proxyTransport != nil {
		client.Transport = c.proxyTransport
	}
	return client
}

// forwardEalRelay sends one EAL CLI call to SimCore for a marked step. The run
// and step are the beacon's, never the caller's. The campaign text is not
// logged.
func (c *BeaconClient) forwardEalRelay(runID, stepID string, req ealrelay.Request) (ealrelay.Response, error) {
	payload := map[string]interface{}{
		"agent_id": c.AgentID, "run_id": runID, "step_id": stepID,
		"verb": req.Verb, "live": req.Live,
	}
	if req.CampaignYAML != "" {
		payload["campaign_yaml"] = req.CampaignYAML
	}
	if req.Plugin != "" {
		payload["plugin"] = req.Plugin
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return ealrelay.Response{}, err
	}
	ctx, cancel := context.WithTimeout(c.stopCtx(), ealRelayTimeout)
	defer cancel()
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.ServerURL+"/api/eal/relay", bytes.NewReader(data))
	if err != nil {
		return ealrelay.Response{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpResp, err := c.ealRelayClient().Do(httpReq)
	if err != nil {
		return ealrelay.Response{}, fmt.Errorf("SimCore did not answer: %w", err)
	}
	defer httpResp.Body.Close()
	body, err := io.ReadAll(httpResp.Body)
	if err != nil {
		return ealrelay.Response{}, fmt.Errorf("SimCore's answer was cut short: %w", err)
	}
	if httpResp.StatusCode != http.StatusOK {
		ref := ealrelay.ParseRefusal(httpResp.StatusCode, body)
		log.Printf("[beacon] EAL relay refused run_id=%s step=%s verb=%s: %s", runID, stepID, req.Verb, ref.Code)
		return ealrelay.Response{}, ref
	}
	var resp ealrelay.Response
	if err := json.Unmarshal(body, &resp); err != nil {
		return ealrelay.Response{}, fmt.Errorf("SimCore's answer is not JSON: %w", err)
	}
	log.Printf("[beacon] EAL relay run_id=%s step=%s verb=%s exit_code=%d operations=%d",
		runID, stepID, req.Verb, resp.ExitCode, len(resp.Ops))
	return resp, nil
}
