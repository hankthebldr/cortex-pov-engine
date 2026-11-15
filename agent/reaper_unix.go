//go:build !windows

package main

// Orphan reaping for the container deployment.
//
// In a pod the beacon runs under a small sh supervisor (PID 1) with
// --subreaper, so grandchildren a step leaves behind (`tool &; exit 0`) reparent
// to the beacon when their parent shell exits. Nothing else waits on them, and
// without this loop they would accumulate as zombies for the pod's lifetime.

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/hankthebldr/cortexsim/agent/executor"
)

// reapSweepInterval re-runs the reaper on a timer: a SIGCHLD that arrived while
// a step was running was skipped, and the orphan it announced must still be
// collected at the next idle moment.
const reapSweepInterval = 2 * time.Second

// wait4 is syscall.Wait4; a package var only so a test can count calls.
var wait4 = syscall.Wait4

// reapOnce collects every exited child that nobody else will Wait on and
// returns how many it collected. It does nothing while a step or chain anchor
// the executor is waiting on is alive (see executor.ReapWhenIdle), so it can
// never take a step's exit status.
func reapOnce() int {
	return executor.ReapWhenIdle(collectZombies)
}

func collectZombies() int {
	n := 0
	for {
		var ws syscall.WaitStatus
		pid, err := wait4(-1, &ws, syscall.WNOHANG, nil)
		if err == syscall.EINTR {
			continue
		}
		if pid <= 0 {
			return n
		}
		n++
	}
}

// startChildReaper runs the reaper until ctx ends. main calls it only with
// --subreaper.
func startChildReaper(ctx context.Context) {
	go childReaperLoop(ctx)
}

func childReaperLoop(ctx context.Context) {
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGCHLD)
	defer signal.Stop(sigCh)
	ticker := time.NewTicker(reapSweepInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-sigCh:
		case <-ticker.C:
		}
		if n := reapOnce(); n > 0 {
			log.Printf("reaped %d orphaned process(es)", n)
		}
	}
}
