package beacon

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/hankthebldr/cortexsim/agent/ealrelay"
)

// The beacon's half of the EAL relay (see agent/ealrelay). SimCore marks a step
// `eal_relay` when its command calls the SimCore-only EAL CLI. Before the task
// runs, the beacon opens a loopback listener, swaps that one call in each
// marked step for its own `eal` subcommand, and forwards whatever the
// subcommand asks to SimCore under this task's run and that step's id.

// ealSelfPath is this beacon's own binary: what a marked step runs instead of
// python. A var so a test can stand a binary in.
var ealSelfPath = os.Executable

// ealRelayTimeout bounds one forwarded call. SimCore runs a collector campaign
// inside the request, so this is far longer than the 30 s API client allows.
const ealRelayTimeout = 10 * time.Minute

// prepareEalRelay rewrites the EAL CLI call in every step the task marked and
// returns the teardown to defer. A task with no marked step is not touched and
// opens nothing.
//
// If the listener cannot start, the commands are left as SimCore sent them: the
// step then fails as it always did on a target, loudly, instead of the beacon
// guessing at another route.
func (c *BeaconClient) prepareEalRelay(task *Task) func() {
	marked := false
	for i := range task.Steps {
		marked = marked || task.Steps[i].EalRelay
	}
	if !marked {
		return func() {}
	}
	exe, err := ealSelfPath()
	var srv *ealrelay.Server
	if err == nil {
		srv, err = ealrelay.Listen(func(stepID string, req ealrelay.Request) (ealrelay.Response, error) {
			return c.forwardEalRelay(task.RunID, stepID, req)
		})
	}
	if err != nil {
		log.Printf("[beacon] EAL relay could not start (run_id=%s): %v — marked steps run their command as sent", task.RunID, err)
		return func() {}
	}
	for i := range task.Steps {
		step := &task.Steps[i]
		if !step.EalRelay {
			continue
		}
		ticket, err := srv.Issue(step.ID)
		if err != nil {
			log.Printf("[beacon] EAL relay: no ticket for step %s: %v", step.ID, err)
			continue
		}
		inv := ealrelay.Invocation{Exe: exe, Relay: srv.Addr(), Ticket: ticket}
		calls := 0
		step.Command, calls = ealrelay.Rewrite(step.Command, beaconGOOS, step.Shell, inv)
		// A variant only ever runs on Windows, under PowerShell (planStep).
		for name, text := range step.PlatformVariants {
			rewritten, n := ealrelay.Rewrite(text, beaconGOOS, "", inv)
			step.PlatformVariants[name] = rewritten
			calls += n
		}
		log.Printf("[beacon] EAL relay: step %s — %d EAL CLI call(s) go through this beacon", step.ID, calls)
	}
	return srv.Close
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
