package main

// The SCM status sequence is the part of the Windows service that decides
// whether `sc.exe query CortexSimAgent` says RUNNING, whether `sc.exe stop`
// returns, and whether the in-flight step gets to post "agent shutdown" before
// the process goes away. It is driven by svcController, which has no syscalls in
// it, so the whole sequence is exercised here on every platform; only the thin
// advapi32 glue in service_windows.go needs a Windows host.

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"
)

type statusRecorder struct {
	mu   sync.Mutex
	got  []svcStatus
	fail error
	seen chan svcStatus
}

func newStatusRecorder() *statusRecorder {
	return &statusRecorder{seen: make(chan svcStatus, 256)}
}

func (r *statusRecorder) report(st svcStatus) error {
	r.mu.Lock()
	r.got = append(r.got, st)
	fail := r.fail
	r.mu.Unlock()
	r.seen <- st
	return fail
}

func (r *statusRecorder) all() []svcStatus {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]svcStatus(nil), r.got...)
}

// waitState blocks until a status with the given state has been reported.
func (r *statusRecorder) waitState(t *testing.T, state uint32) svcStatus {
	t.Helper()
	deadline := time.After(5 * time.Second)
	for {
		select {
		case st := <-r.seen:
			if st.State == state {
				return st
			}
		case <-deadline:
			t.Fatalf("state %s never reported; got %v", svcStateName(state), r.all())
			return svcStatus{}
		}
	}
}

func last(st []svcStatus) svcStatus {
	if len(st) == 0 {
		return svcStatus{}
	}
	return st[len(st)-1]
}

// serveAsync runs ctl.serve(run) in the background and returns its exit code channel.
func serveAsync(ctl *svcController, run func(context.Context) int) <-chan int {
	done := make(chan int, 1)
	go func() { done <- ctl.serve(run) }()
	return done
}

func waitCode(t *testing.T, done <-chan int) int {
	t.Helper()
	select {
	case c := <-done:
		return c
	case <-time.After(5 * time.Second):
		t.Fatal("serve did not return")
		return -1
	}
}

// blockUntilCancelled is the shape of runBeacon: it returns only when its
// context is cancelled.
func blockUntilCancelled(ctx context.Context) int {
	<-ctx.Done()
	return 0
}

// TestServiceLifecycleStartRunningStopPendingStopped: the exact status
// sequence the SCM needs, with the wait hints the beacon's shutdown budget
// relies on.
func TestServiceLifecycleStartRunningStopPendingStopped(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	done := serveAsync(ctl, blockUntilCancelled)

	running := rec.waitState(t, svcRunning)
	if running.Accepts != svcAcceptStop|svcAcceptShutdown {
		t.Errorf("RUNNING accepts 0x%x, want STOP|SHUTDOWN (0x%x)", running.Accepts, svcAcceptStop|svcAcceptShutdown)
	}

	ctl.control(svcControlStop)
	if code := waitCode(t, done); code != 0 {
		t.Errorf("serve returned %d, want 0", code)
	}

	got := rec.all()
	if len(got) < 4 {
		t.Fatalf("want >= 4 status reports, got %v", got)
	}
	if got[0].State != svcStartPending || got[0].WaitHint != 10000 {
		t.Errorf("first report = %+v, want START_PENDING with WaitHint 10000", got[0])
	}
	if got[1].State != svcRunning {
		t.Errorf("second report = %+v, want RUNNING", got[1])
	}
	var stopPending *svcStatus
	for i := range got {
		if got[i].State == svcStopPending {
			stopPending = &got[i]
			break
		}
	}
	if stopPending == nil {
		t.Fatalf("no STOP_PENDING reported: %v", got)
	}
	if stopPending.WaitHint != 45000 {
		t.Errorf("STOP_PENDING WaitHint = %d, want 45000 (2*killGrace + 35 s for the Complete POST)", stopPending.WaitHint)
	}
	if stopPending.CheckPoint == 0 {
		t.Errorf("STOP_PENDING CheckPoint = 0; a pending state must carry a non-zero checkpoint")
	}
	if stopPending.Accepts != 0 {
		t.Errorf("STOP_PENDING still accepts controls 0x%x", stopPending.Accepts)
	}
	end := last(got)
	if end.State != svcStopped || end.Win32ExitCode != 0 || end.SpecificExitCode != 0 {
		t.Errorf("final report = %+v, want STOPPED with NO_ERROR", end)
	}
}

// TestServiceShutdownStopsLikeStop: host shutdown must take the same graceful
// path as an operator stop, so the in-flight step is tree-killed and reported.
func TestServiceShutdownStopsLikeStop(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	var sawCancel bool
	done := serveAsync(ctl, func(ctx context.Context) int {
		<-ctx.Done()
		sawCancel = true
		return 0
	})
	rec.waitState(t, svcRunning)
	ctl.control(svcControlShutdown)
	waitCode(t, done)
	if !sawCancel {
		t.Error("SHUTDOWN did not cancel the run context")
	}
	if end := last(rec.all()); end.State != svcStopped {
		t.Errorf("final report %+v, want STOPPED", end)
	}
}

// TestServiceNonZeroExitIsServiceSpecific: a failing beacon must be visible in
// `sc.exe query` as SERVICE_EXIT_CODE, not read as a clean stop.
func TestServiceNonZeroExitIsServiceSpecific(t *testing.T) {
	// A negative code has no SERVICE_STATUS encoding (the field is a DWORD)
	// and os.Exit(-1) on Windows is 0xFFFFFFFF; both are normalised to 1.
	for _, tc := range []struct {
		code, ret       int
		win32, specific uint32
	}{
		{0, 0, 0, 0},
		{7, 7, 1066, 7},
		{-1, 1, 1066, 1},
	} {
		t.Run(fmt.Sprint(tc.code), func(t *testing.T) {
			rec := newStatusRecorder()
			ctl := newSvcController(rec.report)
			code := tc.code
			got := waitCode(t, serveAsync(ctl, func(context.Context) int { return code }))
			if got != tc.ret {
				t.Errorf("serve returned %d, want %d", got, tc.ret)
			}
			end := last(rec.all())
			if end.State != svcStopped || end.Win32ExitCode != tc.win32 || end.SpecificExitCode != tc.specific {
				t.Errorf("final report %+v, want STOPPED win32=%d specific=%d", end, tc.win32, tc.specific)
			}
		})
	}
}

// TestServiceInterrogateReReportsCurrentStatus: INTERROGATE must answer with
// the current status and change nothing.
func TestServiceInterrogateReReportsCurrentStatus(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	done := serveAsync(ctl, blockUntilCancelled)
	running := rec.waitState(t, svcRunning)

	before := len(rec.all())
	ctl.control(svcControlInterrogate)
	got := rec.all()
	if len(got) != before+1 {
		t.Fatalf("INTERROGATE produced %d reports, want 1", len(got)-before)
	}
	if got[len(got)-1] != running {
		t.Errorf("INTERROGATE reported %+v, want the current RUNNING status %+v", got[len(got)-1], running)
	}
	if ctl.current().State != svcRunning {
		t.Errorf("INTERROGATE changed the state to %s", svcStateName(ctl.current().State))
	}

	ctl.control(svcControlStop)
	waitCode(t, done)
}

// TestServiceIgnoresUnacceptedControls: PAUSE and friends are not accepted, so
// they must neither change state nor report.
func TestServiceIgnoresUnacceptedControls(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	done := serveAsync(ctl, blockUntilCancelled)
	rec.waitState(t, svcRunning)
	before := len(rec.all())
	ctl.control(2) // SERVICE_CONTROL_PAUSE
	ctl.control(3) // SERVICE_CONTROL_CONTINUE
	if n := len(rec.all()); n != before {
		t.Errorf("unaccepted controls produced %d reports", n-before)
	}
	if ctl.current().State != svcRunning {
		t.Errorf("unaccepted control changed state to %s", svcStateName(ctl.current().State))
	}
	ctl.control(svcControlStop)
	waitCode(t, done)
}

// TestServiceStopProgressAdvancesCheckpoint: while the beacon drains, the
// checkpoint keeps moving, which is how the SCM tells "busy" from "hung".
func TestServiceStopProgressAdvancesCheckpoint(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	ctl.progressEvery = 10 * time.Millisecond
	release := make(chan struct{})
	done := serveAsync(ctl, func(ctx context.Context) int {
		<-ctx.Done()
		<-release
		return 0
	})
	rec.waitState(t, svcRunning)
	ctl.control(svcControlStop)
	time.Sleep(80 * time.Millisecond)
	close(release)
	waitCode(t, done)

	var cps []uint32
	for _, st := range rec.all() {
		if st.State == svcStopPending {
			cps = append(cps, st.CheckPoint)
		}
	}
	if len(cps) < 3 {
		t.Fatalf("want >= 3 STOP_PENDING progress reports during an 80 ms drain, got %v", cps)
	}
	for i := 1; i < len(cps); i++ {
		if cps[i] <= cps[i-1] {
			t.Errorf("checkpoint did not advance: %v", cps)
			break
		}
	}
}

// TestServiceStopDeadlineForcesStopped: a beacon wedged past the deadline must
// not leave the service in STOP_PENDING forever — the SCM never kills a
// service on its own, so `sc.exe stop`, a reinstall's `sc.exe delete`, and a
// host shutdown would all hang on it.
func TestServiceStopDeadlineForcesStopped(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	ctl.stopDeadline = 50 * time.Millisecond
	ctl.progressEvery = 10 * time.Millisecond
	var logged []string
	var logMu sync.Mutex
	ctl.logf = func(f string, a ...any) {
		logMu.Lock()
		logged = append(logged, fmt.Sprintf(f, a...))
		logMu.Unlock()
	}
	wedged := make(chan struct{})
	defer close(wedged)
	done := serveAsync(ctl, func(ctx context.Context) int {
		<-wedged // ignores cancellation
		return 0
	})
	rec.waitState(t, svcRunning)
	start := time.Now()
	ctl.control(svcControlStop)
	code := waitCode(t, done)
	if code != exitStopTimedOut {
		t.Errorf("serve returned %d, want exitStopTimedOut (%d)", code, exitStopTimedOut)
	}
	if el := time.Since(start); el > 2*time.Second {
		t.Errorf("deadline of 50ms took %s to fire", el)
	}
	end := last(rec.all())
	if end.State != svcStopped || end.Win32ExitCode != 1066 || end.SpecificExitCode != exitStopTimedOut {
		t.Errorf("final report %+v, want STOPPED 1066/%d", end, exitStopTimedOut)
	}
	logMu.Lock()
	defer logMu.Unlock()
	if len(logged) == 0 {
		t.Error("a forced stop must be logged — it can lose the in-flight run's completion")
	}
	if svcStopDeadline <= svcStopWaitHint {
		t.Errorf("production stop deadline %s must exceed the %s wait hint it backs", svcStopDeadline, svcStopWaitHint)
	}
}

// TestServiceRepeatedStopIsIdempotent: `net stop` retries; a second STOP must
// not panic on a closed channel or cancel twice.
func TestServiceRepeatedStopIsIdempotent(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	release := make(chan struct{})
	done := serveAsync(ctl, func(ctx context.Context) int {
		<-ctx.Done()
		<-release
		return 0
	})
	rec.waitState(t, svcRunning)
	ctl.control(svcControlStop)
	first := rec.waitState(t, svcStopPending)
	ctl.control(svcControlStop)
	ctl.control(svcControlShutdown)
	second := rec.waitState(t, svcStopPending)
	if second.CheckPoint < first.CheckPoint {
		t.Errorf("repeated stop moved the checkpoint backwards: %d -> %d", first.CheckPoint, second.CheckPoint)
	}
	close(release)
	if code := waitCode(t, done); code != 0 {
		t.Errorf("serve returned %d", code)
	}
}

// TestServiceStopBeforeRunningNeverReportsRunning: a stop that races the start
// must not be overwritten by a late RUNNING report.
func TestServiceStopBeforeRunningNeverReportsRunning(t *testing.T) {
	rec := newStatusRecorder()
	ctl := newSvcController(rec.report)
	ctl.control(svcControlStop)
	code := waitCode(t, serveAsync(ctl, blockUntilCancelled))
	if code != 0 {
		t.Errorf("serve returned %d", code)
	}
	for _, st := range rec.all() {
		if st.State == svcRunning {
			t.Errorf("RUNNING reported after a stop request: %v", rec.all())
		}
	}
	if end := last(rec.all()); end.State != svcStopped {
		t.Errorf("final report %+v, want STOPPED", end)
	}
}

// TestServiceReportFailureIsLoggedNotFatal: a failed SetServiceStatus is
// logged and the lifecycle continues — the beacon must not die over it.
func TestServiceReportFailureIsLoggedNotFatal(t *testing.T) {
	rec := newStatusRecorder()
	rec.fail = errors.New("ERROR_INVALID_HANDLE")
	ctl := newSvcController(rec.report)
	var mu sync.Mutex
	var logged []string
	ctl.logf = func(f string, a ...any) {
		mu.Lock()
		logged = append(logged, fmt.Sprintf(f, a...))
		mu.Unlock()
	}
	code := waitCode(t, serveAsync(ctl, func(context.Context) int { return 0 }))
	if code != 0 {
		t.Errorf("serve returned %d", code)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(logged) == 0 {
		t.Error("SetServiceStatus failures were not logged")
	}
}

// TestServiceExitCodePublishedBeforeStopped: the instant the SCM receives
// STOPPED, StartServiceCtrlDispatcherW may return on the main thread and
// runAsService reads the exit code. It must already be there — read from
// inside the STOPPED report itself, which is the earliest the dispatcher can
// possibly return.
func TestServiceExitCodePublishedBeforeStopped(t *testing.T) {
	var ctl *svcController
	var atStopped struct {
		code int
		ok   bool
		seen bool
	}
	ctl = newSvcController(func(st svcStatus) error {
		if st.State == svcStopped {
			atStopped.code, atStopped.ok = ctl.result()
			atStopped.seen = true
		}
		return nil
	})
	if _, ok := ctl.result(); ok {
		t.Fatal("result() published before serve ran")
	}
	if code := waitCode(t, serveAsync(ctl, func(context.Context) int { return 5 })); code != 5 {
		t.Fatalf("serve returned %d", code)
	}
	if !atStopped.seen {
		t.Fatal("STOPPED was never reported")
	}
	if !atStopped.ok || atStopped.code != 5 {
		t.Errorf("at the STOPPED report result() = (%d, %t), want (5, true) — the dispatcher would read a stale code",
			atStopped.code, atStopped.ok)
	}
}
