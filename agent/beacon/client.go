// Package beacon implements the HTTP polling client that connects the Go agent
// to the SimCore orchestrator.  It handles registration, task polling, output
// streaming, and run completion — all over plain JSON/HTTP (no WebSocket, no gRPC).
package beacon

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/hankthebldr/cortexsim/agent/identity"
)

// -------------------------------------------------------------------------
// Domain types
// -------------------------------------------------------------------------

// Task is returned by SimCore when there is work for this agent to perform.
type Task struct {
	RunID      string       `json:"run_id"`
	ScenarioID string       `json:"scenario_id"`
	Command    string       `json:"command"`
	Identity   IdentitySpec `json:"identity"`
}

// IdentitySpec describes which identity harness mode to use for a task.
type IdentitySpec struct {
	// Mode is one of: "direct" | "runuser" | "sudo_u" | "su"
	Mode string `json:"mode"`
	// Username is the service account to impersonate (e.g. "www-data", "postgres", "nobody").
	Username string `json:"username"`
}

// -------------------------------------------------------------------------
// BeaconClient
// -------------------------------------------------------------------------

// BeaconClient manages communication with the SimCore server.
type BeaconClient struct {
	ServerURL string
	AgentID   string
	Interval  time.Duration
	http      *http.Client
}

// New constructs a BeaconClient with a sensible default HTTP timeout.
func New(serverURL, agentID string, interval time.Duration) *BeaconClient {
	return &BeaconClient{
		ServerURL: strings.TrimRight(serverURL, "/"),
		AgentID:   agentID,
		Interval:  interval,
		http: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// -------------------------------------------------------------------------
// API methods
// -------------------------------------------------------------------------

// Register sends agent metadata to SimCore so it appears in the agent roster.
// Corresponds to: POST /api/agents/register
func (c *BeaconClient) Register(hostname, goos string, capabilities []string) error {
	body := map[string]interface{}{
		"agent_id":     c.AgentID,
		"hostname":     hostname,
		"os":           goos,
		"capabilities": capabilities,
	}
	_, err := c.post("/api/agents/register", body)
	if err != nil {
		return fmt.Errorf("register: %w", err)
	}
	return nil
}

// PollTasks asks SimCore whether there is a pending task for this agent.
// Returns (nil, nil) when there is no task (HTTP 404 is treated as "nothing pending").
// Corresponds to: GET /api/agents/{id}/tasks
func (c *BeaconClient) PollTasks() (*Task, error) {
	url := fmt.Sprintf("%s/api/agents/%s/tasks", c.ServerURL, c.AgentID)

	resp, err := c.http.Get(url)
	if err != nil {
		return nil, fmt.Errorf("pollTasks GET: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNotFound {
		// 404 → no task queued; this is normal.
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("pollTasks: unexpected status %d: %s", resp.StatusCode, b)
	}

	var task Task
	if err := json.NewDecoder(resp.Body).Decode(&task); err != nil {
		return nil, fmt.Errorf("pollTasks decode: %w", err)
	}
	return &task, nil
}

// SendOutput streams partial or final command output back to SimCore.
// Corresponds to: POST /api/runs/{run_id}/output
func (c *BeaconClient) SendOutput(runID, output string) error {
	body := map[string]string{
		"run_id": runID,
		"output": output,
	}
	_, err := c.post(fmt.Sprintf("/api/runs/%s/output", runID), body)
	if err != nil {
		return fmt.Errorf("sendOutput: %w", err)
	}
	return nil
}

// Complete marks a run as finished with its exit code and a human-readable summary.
// Corresponds to: POST /api/runs/{run_id}/complete
func (c *BeaconClient) Complete(runID string, exitCode int, summary string) error {
	body := map[string]interface{}{
		"run_id":    runID,
		"exit_code": exitCode,
		"summary":   summary,
	}
	_, err := c.post(fmt.Sprintf("/api/runs/%s/complete", runID), body)
	if err != nil {
		return fmt.Errorf("complete: %w", err)
	}
	return nil
}

// -------------------------------------------------------------------------
// Main poll loop
// -------------------------------------------------------------------------

// Run is the agent's main execution loop.  It polls for tasks on every tick,
// executes them via the identity harness, streams output every 5 seconds or on
// completion, then reports the final exit code.  It exits cleanly when ctx is
// cancelled (SIGINT / SIGTERM).
func (c *BeaconClient) Run(ctx context.Context) {
	ticker := time.NewTicker(c.Interval)
	defer ticker.Stop()

	log.Printf("[beacon] agent %q started — polling %s every %s", c.AgentID, c.ServerURL, c.Interval)

	for {
		select {
		case <-ctx.Done():
			log.Printf("[beacon] context cancelled — shutting down cleanly")
			return

		case <-ticker.C:
			task, err := c.PollTasks()
			if err != nil {
				log.Printf("[beacon] poll error: %v", err)
				continue
			}
			if task == nil {
				log.Printf("[beacon] no task pending")
				continue
			}

			log.Printf("[beacon] received task run_id=%s scenario=%s mode=%s user=%s",
				task.RunID, task.ScenarioID, task.Identity.Mode, task.Identity.Username)

			c.executeTask(ctx, task)
		}
	}
}

// -------------------------------------------------------------------------
// Task execution helpers
// -------------------------------------------------------------------------

// executeTask runs a single task through the identity harness, streams output,
// and calls Complete when finished.
func (c *BeaconClient) executeTask(ctx context.Context, task *Task) {
	execID := identity.ExecutionIdentity{
		Mode:     task.Identity.Mode,
		Username: task.Identity.Username,
		Command:  task.Command,
	}

	// Run the command in a goroutine so we can stream output while it executes.
	type result struct {
		res identity.ExecResult
		err error
	}
	done := make(chan result, 1)

	go func() {
		r, e := identity.Execute(execID)
		done <- result{r, e}
	}()

	// Stream intermediate output every 5 seconds until the command finishes.
	streamTicker := time.NewTicker(5 * time.Second)
	defer streamTicker.Stop()

	var finalResult result
	var outputBuffer strings.Builder

loop:
	for {
		select {
		case <-ctx.Done():
			// Context cancelled mid-execution; report what we have.
			log.Printf("[beacon] context cancelled during task run_id=%s", task.RunID)
			_ = c.Complete(task.RunID, -1, "agent shutdown — task interrupted")
			return

		case <-streamTicker.C:
			// Intermediate keep-alive flush — we don't have partial output yet
			// (exec.Command buffers internally), so send an empty heartbeat.
			snapshot := outputBuffer.String()
			if snapshot != "" {
				if err := c.SendOutput(task.RunID, snapshot); err != nil {
					log.Printf("[beacon] sendOutput (intermediate) error: %v", err)
				}
			}

		case r := <-done:
			finalResult = r
			break loop
		}
	}

	if finalResult.err != nil {
		log.Printf("[beacon] execution error run_id=%s: %v", task.RunID, finalResult.err)
		// Send whatever partial output we may have and mark failed.
		_ = c.SendOutput(task.RunID, fmt.Sprintf("ERROR: %v", finalResult.err))
		_ = c.Complete(task.RunID, -1, fmt.Sprintf("execution error: %v", finalResult.err))
		return
	}

	res := finalResult.res
	combined := combineOutput(res.Stdout, res.Stderr)
	log.Printf("[beacon] task complete run_id=%s exit_code=%d duration=%s",
		task.RunID, res.ExitCode, res.Duration)

	// Send full output.
	if err := c.SendOutput(task.RunID, combined); err != nil {
		log.Printf("[beacon] sendOutput (final) error: %v", err)
	}

	summary := buildSummary(task, res)
	if err := c.Complete(task.RunID, res.ExitCode, summary); err != nil {
		log.Printf("[beacon] complete error: %v", err)
	}
}

// -------------------------------------------------------------------------
// Internal helpers
// -------------------------------------------------------------------------

// post marshals body as JSON and POSTs it to path on the SimCore server.
// Returns the raw response body on success (2xx).
func (c *BeaconClient) post(path string, body interface{}) ([]byte, error) {
	data, err := json.Marshal(body)
	if err != nil {
		return nil, fmt.Errorf("marshal: %w", err)
	}

	resp, err := c.http.Post(
		c.ServerURL+path,
		"application/json",
		bytes.NewReader(data),
	)
	if err != nil {
		return nil, fmt.Errorf("POST %s: %w", path, err)
	}
	defer resp.Body.Close()

	respBody, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("POST %s: status %d: %s", path, resp.StatusCode, respBody)
	}
	return respBody, nil
}

// combineOutput merges stdout and stderr into a single log-friendly string.
func combineOutput(stdout, stderr string) string {
	var b strings.Builder
	if stdout != "" {
		b.WriteString("=== STDOUT ===\n")
		b.WriteString(stdout)
	}
	if stderr != "" {
		if b.Len() > 0 {
			b.WriteString("\n")
		}
		b.WriteString("=== STDERR ===\n")
		b.WriteString(stderr)
	}
	return b.String()
}

// buildSummary creates a human-readable completion summary.
func buildSummary(task *Task, res identity.ExecResult) string {
	status := "SUCCESS"
	if res.ExitCode != 0 {
		status = fmt.Sprintf("FAILED (exit %d)", res.ExitCode)
	}
	return fmt.Sprintf("%s | scenario=%s mode=%s duration=%s",
		status, task.ScenarioID, task.Identity.Mode, res.Duration.Round(time.Millisecond))
}
