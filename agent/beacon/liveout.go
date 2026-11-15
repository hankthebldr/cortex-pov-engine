package beacon

// Live runtime log: the beacon tells SimCore which command a step is about to
// run, and streams the step's output while it runs.
//
// Before this the beacon posted one frame per step, after the step ended, and
// never reported the command. A three-minute step was silent for three minutes.
//
// It is driven by the task, not by a flag. When SimCore sends `live_output`
// the beacon posts two more kinds of /output frame beside the final one:
//
//	{"kind":"start","seq":0,"step_id":…,"output":"","command":…,"identity":…,"index":n,"total":N}
//	{"kind":"chunk","seq":k,"step_id":…,"output":"…"}
//
// SimCore publishes both and stores neither. The final per-step frame
// (SendOutput, no `kind`) is built and sent exactly as before whether or not
// any live frame was delivered, so the stored record does not depend on this
// file. An older SimCore sends no `live_output`, and then nothing here runs.
//
// The rule every function below keeps: a live POST never holds a step up. The
// step's pipe writers only append to a bounded buffer; one goroutine per step
// does the posting; a slow or failing SimCore loses live frames, never output.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"sync"
	"sync/atomic"
	"time"
	"unicode/utf8"

	"github.com/hankthebldr/cortexsim/agent/executor"
)

// LiveOutput is the task's `live_output` block. A zero or negative value takes
// the default beside it.
type LiveOutput struct {
	// FlushMs is how often buffered output is posted. Default 1000.
	FlushMs int `json:"flush_ms"`
	// MaxChunkBytes posts a chunk early once this much is waiting, and is the
	// most one chunk carries. Default 16384.
	MaxChunkBytes int `json:"max_chunk_bytes"`
	// MaxStepBytes is how much of one step's output is streamed. Past it the
	// step keeps running and its final frame still carries everything.
	// Default 262144.
	MaxStepBytes int `json:"max_step_bytes"`
}

const (
	defaultLiveFlushMs       = 1000
	defaultLiveMaxChunkBytes = 16384
	defaultLiveMaxStepBytes  = 262144
)

// liveCapNote ends the live stream of a step that printed more than
// MaxStepBytes, so a reader knows the output did not stop there.
const liveCapNote = "\n[live output stops here: the step printed more than the live limit. Its full output is stored when the step ends.]\n"

// liveStartWait is the longest a step waits for SimCore to acknowledge its
// start frame before it runs anyway. A healthy SimCore answers in
// milliseconds, so the command is on record before the step's first process
// exists; a slow one costs the step at most this. A var only so tests can
// change it.
var liveStartWait = 250 * time.Millisecond

// livePostTimeout bounds one live POST. A var only so tests can change it.
var livePostTimeout = 5 * time.Second

// liveSender posts the live frames of one task. A nil *liveSender is valid and
// does nothing, which is how a task without `live_output` runs.
type liveSender struct {
	c     *BeaconClient
	runID string
	flush time.Duration
	chunk int
	step  int

	// ctx ends with the task, or at once when the beacon is told to stop.
	ctx context.Context
	// off is set when SimCore refuses a live frame outright (4xx): it will
	// refuse the rest too, so the task stops sending them.
	off atomic.Bool
	// cur is the step now running. Only the task's goroutine touches it.
	cur *liveStep
}

// beginLive starts live output for a task that asks for it and returns the
// function that ends it. For a task without `live_output` both are no-ops.
func (c *BeaconClient) beginLive(task *Task) (end func()) {
	if task.LiveOutput == nil {
		c.live = nil
		return func() {}
	}
	positive := func(v, def int) int {
		if v > 0 {
			return v
		}
		return def
	}
	ctx, cancel := context.WithCancel(c.stopCtx())
	c.live = &liveSender{
		c:     c,
		runID: task.RunID,
		flush: time.Duration(positive(task.LiveOutput.FlushMs, defaultLiveFlushMs)) * time.Millisecond,
		chunk: positive(task.LiveOutput.MaxChunkBytes, defaultLiveMaxChunkBytes),
		step:  positive(task.LiveOutput.MaxStepBytes, defaultLiveMaxStepBytes),
		ctx:   ctx,
	}
	return func() {
		c.live.stepEnd()
		cancel()
		c.live = nil
	}
}

// stepStart posts the start frame for a step that is about to run and opens
// its live stream. It returns once SimCore has the frame or liveStartWait has
// passed, whichever is first.
func (l *liveSender) stepStart(index, total int, step *Step, username string) {
	if l == nil {
		return
	}
	l.stepEnd()
	ctx, cancel := context.WithCancel(l.ctx)
	s := &liveStep{l: l, stepID: step.ID, ctx: ctx, cancel: cancel, wake: make(chan struct{}, 1)}
	l.cur = s
	start := map[string]interface{}{
		"run_id": l.runID, "kind": "start", "seq": 0, "step_id": step.ID, "output": "",
		"command": step.Command, "identity": username, "index": index, "total": total,
	}
	acked := make(chan struct{})
	go s.run(start, acked)
	wait := time.NewTimer(liveStartWait)
	defer wait.Stop()
	select {
	case <-acked:
	case <-wait.C:
	}
}

// chunkFunc is the output callback for the step now running: nil when live
// output is off, so the executor captures exactly as it does without it.
func (l *liveSender) chunkFunc(stepID string) executor.OutputChunkFunc {
	if l == nil || l.cur == nil || l.cur.stepID != stepID {
		return nil
	}
	return l.cur.write
}

// stepEnd closes the running step's live stream. It does not wait: a live POST
// still in flight is abandoned, and whatever it or the buffer held is in the
// final frame the caller sends next.
func (l *liveSender) stepEnd() {
	if l == nil || l.cur == nil {
		return
	}
	l.cur.cancel()
	l.cur = nil
}

// post sends one live frame, once. Failure is not reported to the caller:
// there is nothing a step could do about it.
func (l *liveSender) post(ctx context.Context, frame map[string]interface{}) {
	if l.off.Load() || ctx.Err() != nil {
		return
	}
	data, err := json.Marshal(frame)
	if err != nil {
		return
	}
	reqCtx, cancel := context.WithTimeout(ctx, livePostTimeout)
	defer cancel()
	url := fmt.Sprintf("%s/api/runs/%s/output", l.c.ServerURL, l.runID)
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, url, bytes.NewReader(data))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := l.c.http.Do(req)
	if err != nil {
		return
	}
	_, _ = io.Copy(io.Discard, resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode >= 400 && resp.StatusCode < 500 && l.off.CompareAndSwap(false, true) {
		log.Printf("[beacon] live output refused by SimCore (status %d, run_id=%s) — no further live frames for this task; "+
			"each step's full output is still sent when it ends", resp.StatusCode, l.runID)
	}
}

// liveStep is one step's live stream.
type liveStep struct {
	l      *liveSender
	stepID string
	// ctx ends when the step does.
	ctx    context.Context
	cancel context.CancelFunc
	// wake asks for a flush before the next tick: a full chunk is waiting.
	wake chan struct{}

	mu      sync.Mutex
	pending []byte // accepted, not yet posted; never more than l.step (+ the note)
	taken   int    // bytes of step output accepted so far
	capped  bool
	seq     int
}

// write is the executor's output callback. It runs on the goroutines copying
// the step's stdout and stderr, so it only appends to memory.
func (s *liveStep) write(chunk string, _ bool) {
	s.mu.Lock()
	if s.capped {
		s.mu.Unlock()
		return
	}
	if room := s.l.step - s.taken; len(chunk) > room {
		chunk = chunk[:room]
		s.capped = true
	}
	s.pending = append(s.pending, chunk...)
	s.taken += len(chunk)
	if s.capped {
		s.pending = append(s.pending, liveCapNote...)
	}
	full := len(s.pending) >= s.l.chunk || s.capped
	s.mu.Unlock()
	if full {
		select {
		case s.wake <- struct{}{}:
		default:
		}
	}
}

// run posts the start frame, then the step's output: every flush interval, or
// sooner when a full chunk is waiting. It ends with the step.
func (s *liveStep) run(start map[string]interface{}, acked chan<- struct{}) {
	s.l.post(s.ctx, start)
	close(acked)

	tick := time.NewTicker(s.l.flush)
	defer tick.Stop()
	for {
		select {
		case <-s.ctx.Done():
			return
		case <-tick.C:
		case <-s.wake:
		}
		for s.flush() {
		}
	}
}

// flush posts at most one chunk. It reports whether another full one is waiting.
func (s *liveStep) flush() (more bool) {
	s.mu.Lock()
	n := len(s.pending)
	if n == 0 {
		s.mu.Unlock()
		return false
	}
	if n > s.l.chunk {
		n = s.l.chunk
		// Do not split a character between two chunks.
		for cut := n; cut > n-utf8.UTFMax && cut > 0; cut-- {
			if utf8.RuneStart(s.pending[cut]) {
				n = cut
				break
			}
		}
	}
	text := string(s.pending[:n])
	s.pending = append(s.pending[:0:0], s.pending[n:]...)
	more = len(s.pending) >= s.l.chunk
	s.seq++
	seq := s.seq
	s.mu.Unlock()

	s.l.post(s.ctx, map[string]interface{}{
		"run_id": s.l.runID, "kind": "chunk", "seq": seq, "step_id": s.stepID, "output": text,
	})
	return more && s.ctx.Err() == nil
}
