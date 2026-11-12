package main

// svcController is the Windows service status state machine with the syscalls
// taken out. service_windows.go owns the advapi32 glue (dispatcher, handler
// registration, SetServiceStatus); everything that decides WHAT the SCM is
// told, and WHEN, lives here so it is exercised on every platform's CI rather
// than only on a Windows runner. Nothing here is Windows-specific except the
// numeric values, which are the documented Win32 constants.
//
// The sequence the SCM requires, and what each step buys the beacon:
//
//	START_PENDING (wait hint 10 s)  the dispatcher connected inside the 30 s budget
//	RUNNING (accepts STOP|SHUTDOWN) `sc.exe query` says RUNNING; the beacon is polling
//	STOP_PENDING (wait hint 45 s)   ctx cancelled: the in-flight step is tree-killed and
//	                                its "agent shutdown" Complete POST gets time to land
//	STOPPED (exit code)             0 → NO_ERROR, n → ERROR_SERVICE_SPECIFIC_ERROR / n

import (
	"context"
	"log"
	"sync"
	"sync/atomic"
	"time"
)

// SERVICE_STATUS.dwCurrentState values.
const (
	svcStopped      uint32 = 1
	svcStartPending uint32 = 2
	svcStopPending  uint32 = 3
	svcRunning      uint32 = 4
)

// SERVICE_STATUS.dwControlsAccepted bits.
const (
	svcAcceptStop     uint32 = 0x1
	svcAcceptShutdown uint32 = 0x4
)

// HandlerEx dwControl values the beacon acts on. Everything else (PAUSE,
// CONTINUE, PARAMCHANGE, ...) is not accepted, so the SCM refuses it before it
// reaches the handler; if one arrives anyway it is ignored.
const (
	svcControlStop        uint32 = 1
	svcControlInterrogate uint32 = 4
	svcControlShutdown    uint32 = 5
)

// errServiceSpecificError is ERROR_SERVICE_SPECIFIC_ERROR: Win32ExitCode says
// "look at ServiceSpecificExitCode", which carries the beacon's own exit code.
const errServiceSpecificError uint32 = 1066

const (
	// svcStartWaitHint covers START_PENDING → RUNNING, which is immediate: the
	// beacon's network startup (Register can block for its 30 s HTTP timeout)
	// runs AFTER RUNNING is reported, never inside the start budget.
	svcStartWaitHint = 10 * time.Second

	// svcStopWaitHint is 2*killGrace (3 s each: the step's tree kill, and the
	// chained session's) plus 35 s for the "agent shutdown" Complete POST,
	// which carries the beacon's 30 s HTTP client timeout.
	svcStopWaitHint = 45 * time.Second

	// svcStopProgressEvery advances the STOP_PENDING checkpoint while the
	// beacon drains, which is how the SCM distinguishes busy from hung.
	svcStopProgressEvery = 5 * time.Second

	// svcStopDeadline is the systemd TimeoutStopSec analogue. The SCM never
	// kills a service that stays in STOP_PENDING, so without this a wedged
	// beacon would hang `sc.exe stop`, a reinstall (sc.exe delete only marks
	// a running service for deletion, and the following sc.exe create fails
	// with 1072), and host shutdown. It is deliberately longer than the wait
	// hint so it only fires on a beacon that is genuinely stuck.
	svcStopDeadline = 60 * time.Second
)

// exitStopTimedOut is the service-specific exit code reported when the beacon
// did not stop within svcStopDeadline. `sc.exe query CortexSimAgent` shows it
// as SERVICE_EXIT_CODE : 3 (0x3) under WIN32_EXIT_CODE : 1066.
const exitStopTimedOut = 3

// svcStatus is the platform-neutral mirror of Win32 SERVICE_STATUS (without
// dwServiceType, which is always SERVICE_WIN32_OWN_PROCESS).
type svcStatus struct {
	State            uint32
	Accepts          uint32
	Win32ExitCode    uint32
	SpecificExitCode uint32
	CheckPoint       uint32
	WaitHint         uint32 // milliseconds
}

func svcStateName(s uint32) string {
	switch s {
	case svcStopped:
		return "STOPPED"
	case svcStartPending:
		return "START_PENDING"
	case svcStopPending:
		return "STOP_PENDING"
	case svcRunning:
		return "RUNNING"
	}
	return "UNKNOWN"
}

func waitHintMillis(d time.Duration) uint32 { return uint32(d / time.Millisecond) }

// svcController drives one service lifetime. report is SetServiceStatus in
// production and a recorder in tests; it is always called with mu held, so the
// SCM sees statuses in the order they were decided.
type svcController struct {
	report func(svcStatus) error
	logf   func(format string, args ...any)

	// Tunable for tests; production values are the svc* constants.
	stopDeadline  time.Duration
	progressEvery time.Duration

	ctx    context.Context
	cancel context.CancelFunc

	mu       sync.Mutex
	cur      svcStatus
	stopOnce sync.Once
	stopReq  chan struct{}

	// The exit code is published BEFORE STOPPED is reported: once the SCM
	// has STOPPED for the process's only service, StartServiceCtrlDispatcherW
	// returns on the main thread — possibly before serve's own thread runs
	// another instruction — and runAsService reads the code from here.
	exited   atomic.Bool
	exitCode atomic.Int64
}

// result is the exit code serve published, and whether it has published one.
func (c *svcController) result() (int, bool) {
	if !c.exited.Load() {
		return 0, false
	}
	return int(c.exitCode.Load()), true
}

func newSvcController(report func(svcStatus) error) *svcController {
	ctx, cancel := context.WithCancel(context.Background())
	return &svcController{
		report:        report,
		logf:          log.Printf,
		stopDeadline:  svcStopDeadline,
		progressEvery: svcStopProgressEvery,
		ctx:           ctx,
		cancel:        cancel,
		stopReq:       make(chan struct{}),
	}
}

func (c *svcController) current() svcStatus {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.cur
}

// reportLocked sends the current status to the SCM. A failed SetServiceStatus
// is logged and otherwise survived: there is nothing better to do with it, and
// killing the beacon over it would turn a status glitch into an outage.
func (c *svcController) reportLocked() {
	if err := c.report(c.cur); err != nil {
		c.logf("[service] SetServiceStatus(%s) failed: %v", svcStateName(c.cur.State), err)
	}
}

// serve runs the service: START_PENDING, then run on its own goroutine, then
// RUNNING, then — when run returns or a stop is requested — STOPPED with run's
// exit code. It returns that code (negative codes are normalised to 1: the
// SERVICE_STATUS field is a DWORD).
func (c *svcController) serve(run func(ctx context.Context) int) int {
	defer c.cancel()

	c.mu.Lock()
	if c.cur.State == 0 { // a stop that raced the start already set STOP_PENDING
		c.cur = svcStatus{State: svcStartPending, CheckPoint: 1, WaitHint: waitHintMillis(svcStartWaitHint)}
		c.reportLocked()
	}
	c.mu.Unlock()

	done := make(chan int, 1)
	go func() { done <- run(c.ctx) }()

	c.mu.Lock()
	if c.cur.State == svcStartPending {
		c.cur = svcStatus{State: svcRunning, Accepts: svcAcceptStop | svcAcceptShutdown}
		c.reportLocked()
	}
	c.mu.Unlock()

	var code int
	select {
	case code = <-done:
	case <-c.stopReq:
		code = c.awaitStop(done)
	}
	if code < 0 {
		code = 1
	}
	c.exitCode.Store(int64(code))
	c.exited.Store(true)

	c.mu.Lock()
	c.cur = svcStatus{State: svcStopped}
	if code != 0 {
		c.cur.Win32ExitCode = errServiceSpecificError
		c.cur.SpecificExitCode = uint32(code)
	}
	c.reportLocked()
	c.mu.Unlock()
	return code
}

// awaitStop waits for run to drain after a stop request, advancing the
// STOP_PENDING checkpoint, and gives up at the stop deadline.
func (c *svcController) awaitStop(done <-chan int) int {
	deadline := time.NewTimer(c.stopDeadline)
	defer deadline.Stop()
	tick := time.NewTicker(c.progressEvery)
	defer tick.Stop()
	for {
		select {
		case code := <-done:
			return code
		case <-tick.C:
			c.mu.Lock()
			if c.cur.State == svcStopPending {
				c.cur.CheckPoint++
				c.reportLocked()
			}
			c.mu.Unlock()
		case <-deadline.C:
			c.logf("[service] the beacon did not stop within %s of the stop request — reporting STOPPED "+
				"with service exit code %d; an in-flight run may not have posted its completion and "+
				"will read as orphaned in SimCore", c.stopDeadline, exitStopTimedOut)
			return exitStopTimedOut
		}
	}
}

// control is HandlerEx's body. It must return quickly (the SCM allows a
// handler 30 s, and it runs on the dispatcher thread), so a stop only reports
// STOP_PENDING and cancels; serve does the waiting.
func (c *svcController) control(ctl uint32) {
	switch ctl {
	case svcControlStop, svcControlShutdown:
		first := false
		c.stopOnce.Do(func() {
			first = true
			c.mu.Lock()
			c.cur.State = svcStopPending
			c.cur.Accepts = 0
			c.cur.CheckPoint++
			c.cur.WaitHint = waitHintMillis(svcStopWaitHint)
			c.reportLocked()
			c.mu.Unlock()
			// The beacon's existing cancellation path does the rest: the
			// in-flight step's process tree is killed and the run is completed
			// as "agent shutdown — task interrupted".
			c.cancel()
			close(c.stopReq)
		})
		if !first {
			// `net stop` retries; answer with where the stop has got to.
			c.mu.Lock()
			c.reportLocked()
			c.mu.Unlock()
		}
	case svcControlInterrogate:
		c.mu.Lock()
		c.reportLocked()
		c.mu.Unlock()
	}
}
