//go:build windows

package main

// These run only on a Windows host (the windows-native CI job). They cover the
// advapi32 glue that service_state_test.go cannot reach: that a console
// process — which is what `go test` is — gets ERROR_FAILED_SERVICE_CONTROLLER_
// CONNECT back from StartServiceCtrlDispatcherW and falls through to the
// console path rather than hanging or claiming to be a service, and that the
// structs handed to the SCM have the Win32 layout.
//
// What they do NOT prove: that the SCM starts the installed service, that it
// reaches RUNNING, or that `sc.exe stop` takes it to STOPPED. That needs a
// real `sc.exe create` + `sc.exe start` against the built exe, which belongs to
// the windows-native CI job's end-to-end step.

import (
	"context"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
	"unsafe"
)

// TestRunAsServiceFromConsoleFallsBack: the 1063 fallback is the only reason a
// DC can still run cortexsim-agent.exe by hand from a PowerShell prompt.
func TestRunAsServiceFromConsoleFallsBack(t *testing.T) {
	type result struct {
		handled bool
		code    int
		err     error
	}
	called := make(chan struct{}, 1)
	res := make(chan result, 1)
	go func() {
		h, c, err := runAsService(serviceName, func(context.Context) int {
			called <- struct{}{}
			return 0
		})
		res <- result{h, c, err}
	}()

	select {
	case r := <-res:
		if r.handled {
			t.Errorf("runAsService from a console process reported handled=true (code=%d err=%v)", r.code, r.err)
		}
		if r.err != nil {
			t.Errorf("runAsService from a console process returned err=%v, want the silent 1063 fallback", r.err)
		}
		if r.code != 0 {
			t.Errorf("runAsService returned code=%d on fallback", r.code)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("StartServiceCtrlDispatcherW did not return within 20 s for a console process — " +
			"a hand-run beacon would hang before it ever polled")
	}
	select {
	case <-called:
		t.Error("the beacon run func was invoked although no SCM connected")
	default:
	}
	if underSCM() {
		t.Error("underSCM() is true in a console process")
	}
}

// TestServiceStructLayoutsMatchWin32: SERVICE_STATUS is seven DWORDs and
// SERVICE_TABLE_ENTRYW is two pointers. A wrong size here is memory
// corruption inside services.exe's view of us, not a compile error.
func TestServiceStructLayoutsMatchWin32(t *testing.T) {
	if got := unsafe.Sizeof(serviceStatusW{}); got != 28 {
		t.Errorf("sizeof(SERVICE_STATUS) = %d, want 28", got)
	}
	if got, want := unsafe.Sizeof(serviceTableEntry{}), 2*unsafe.Sizeof(uintptr(0)); got != want {
		t.Errorf("sizeof(SERVICE_TABLE_ENTRYW) = %d, want %d", got, want)
	}
}

func TestAdvapi32ServiceProcsResolve(t *testing.T) {
	for _, p := range []*syscall.LazyProc{
		procStartServiceCtrlDispatcherW,
		procRegisterServiceCtrlHandlerExW,
		procSetServiceStatus,
	} {
		if err := p.Find(); err != nil {
			t.Errorf("advapi32!%s does not resolve: %v", p.Name, err)
		}
	}
}

func TestUTF16PtrToStringRoundTrip(t *testing.T) {
	for _, s := range []string{"CortexSimAgent", "", "Ünïcode-svc"} {
		p, err := syscall.UTF16PtrFromString(s)
		if err != nil {
			t.Fatal(err)
		}
		if got := utf16PtrToString(p); got != s {
			t.Errorf("utf16PtrToString round trip = %q, want %q", got, s)
		}
	}
	if got := utf16PtrToString(nil); got != "" {
		t.Errorf("utf16PtrToString(nil) = %q", got)
	}
}

// TestServiceDefaultLogPathMatchesInstaller: the installer computes the log
// path with PowerShell's Join-Path over $env:ProgramData; the beacon must land
// on the byte-identical file on a real host, not merely on a hard-coded C:.
func TestServiceDefaultLogPathMatchesInstaller(t *testing.T) {
	pd := os.Getenv("ProgramData")
	if pd == "" {
		t.Skip("ProgramData is not set in this environment")
	}
	want := filepath.Join(pd, "CortexSim", "agent.log")
	if got := resolveLogPath("", "", true, pd); got != want {
		t.Errorf("service default log path = %q, want %q", got, want)
	}
}
