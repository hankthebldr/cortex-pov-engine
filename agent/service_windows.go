//go:build windows

package main

// The Windows service control manager glue, stdlib only.
//
// The elevated Windows installer registers cortexsim-agent.exe as the
// auto-start LocalSystem service CortexSimAgent. The SCM then waits up to 30 s
// (ServicesPipeTimeout) for the new process to call StartServiceCtrlDispatcherW;
// a process that never does is failed with ERROR_SERVICE_REQUEST_TIMEOUT (1053)
// and torn down. Before this file nothing in the beacon called it, so every
// elevated install reported success and died about 30 s later, and again after
// every reboot.
//
// go.mod stays dependency-free, so instead of golang.org/x/sys/windows/svc this
// is the same three advapi32 calls through syscall, with svcController
// (service_state.go) deciding what to report. syscall.NewCallback works with
// CGO_ENABLED=0 — the Go runtime on Windows can always run a callback on a
// thread it did not create, which is what the SCM's ServiceMain thread is.
//
// Not proven by anything that runs on a non-Windows host: that the SCM starts
// the service, that it reaches RUNNING, and that `sc.exe stop` takes it to
// STOPPED. service_windows_test.go proves the console fallback and the struct
// layouts on a Windows runner; the end-to-end start/stop belongs to the
// windows-native CI job.

import (
	"context"
	"errors"
	"fmt"
	"log"
	"os"
	"runtime"
	"sync"
	"sync/atomic"
	"syscall"
	"unicode/utf16"
	"unsafe"
)

const (
	serviceWin32OwnProcess = 0x10

	// errFailedServiceControllerConnect is what StartServiceCtrlDispatcherW
	// returns when the process was not started by the SCM — i.e. a DC ran the
	// exe by hand, or the non-elevated installer started it detached.
	errFailedServiceControllerConnect = syscall.Errno(1063)

	// exitServiceRegisterFailed: RegisterServiceCtrlHandlerExW failed inside
	// ServiceMain, so no status can ever be reported.
	exitServiceRegisterFailed = 4

	// maxServiceNameChars bounds the argv[0] scan (the SCM caps service
	// names at 256 characters).
	maxServiceNameChars = 256
)

// advapi32 is a KnownDLL (always mapped from System32) and is already loaded
// by the syscall package itself, so resolving it by bare name cannot be
// hijacked by a DLL planted next to the exe.
var (
	advapi32                          = syscall.NewLazyDLL("advapi32.dll")
	procStartServiceCtrlDispatcherW   = advapi32.NewProc("StartServiceCtrlDispatcherW")
	procRegisterServiceCtrlHandlerExW = advapi32.NewProc("RegisterServiceCtrlHandlerExW")
	procSetServiceStatus              = advapi32.NewProc("SetServiceStatus")
)

// serviceTableEntry is SERVICE_TABLE_ENTRYW.
type serviceTableEntry struct {
	ServiceName *uint16
	ServiceProc uintptr
}

// serviceStatusW is SERVICE_STATUS: seven DWORDs.
type serviceStatusW struct {
	ServiceType             uint32
	CurrentState            uint32
	ControlsAccepted        uint32
	Win32ExitCode           uint32
	ServiceSpecificExitCode uint32
	CheckPoint              uint32
	WaitHint                uint32
}

var (
	callbacksOnce       sync.Once
	serviceMainCallback uintptr
	handlerCallback     uintptr

	scmConnected     atomic.Bool
	activeController atomic.Pointer[svcController]

	// theService carries runAsService's arguments into ServiceMain. The SCM
	// callbacks carry no Go state, so this is a package global — one service
	// per process, which is all the beacon is. The exit code travels back
	// through the controller (svcController.result), which publishes it
	// before reporting STOPPED; a field written after serve returns would
	// race the dispatcher's own return on the main thread.
	theService struct {
		mu   sync.Mutex
		name string
		run  func(context.Context) int
	}
)

// underSCM reports whether the service control manager connected to this
// process (ServiceMain has been called).
func underSCM() bool { return scmConnected.Load() }

// runAsService hands this process to the service control manager. Under the
// SCM it blocks until the service has stopped and returns handled=true with
// run's exit code. From a console (StartServiceCtrlDispatcherW fails with
// 1063) it returns handled=false at once and run is never called, so main
// falls through to the console path. Any other failure is handled=true with
// the error: the process is a service that cannot be served.
func runAsService(name string, run func(ctx context.Context) int) (handled bool, exitCode int, err error) {
	namePtr, err := syscall.UTF16PtrFromString(name)
	if err != nil {
		return true, 1, fmt.Errorf("service name %q: %w", name, err)
	}
	if err := procStartServiceCtrlDispatcherW.Find(); err != nil {
		return true, 1, fmt.Errorf("advapi32!StartServiceCtrlDispatcherW unavailable: %w", err)
	}
	callbacksOnce.Do(func() {
		serviceMainCallback = syscall.NewCallback(serviceMain)
		handlerCallback = syscall.NewCallback(serviceHandler)
	})

	theService.mu.Lock()
	theService.name = name
	theService.run = run
	theService.mu.Unlock()
	activeController.Store(nil)

	// The table must end with a NULL entry. For SERVICE_WIN32_OWN_PROCESS the
	// SCM ignores the name, but it must not be NULL.
	table := [2]serviceTableEntry{
		{ServiceName: namePtr, ServiceProc: serviceMainCallback},
		{},
	}

	// The dispatcher thread is where HandlerEx is called back; keep this
	// goroutine on it for the whole (blocking) dispatcher lifetime.
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()

	r1, _, callErr := procStartServiceCtrlDispatcherW.Call(uintptr(unsafe.Pointer(&table[0])))
	runtime.KeepAlive(&table)
	runtime.KeepAlive(namePtr)
	if r1 == 0 {
		var errno syscall.Errno
		if errors.As(callErr, &errno) && errno == errFailedServiceControllerConnect {
			return false, 0, nil
		}
		return true, 1, fmt.Errorf("StartServiceCtrlDispatcherW: %w", callErr)
	}
	ctl := activeController.Load()
	if ctl == nil {
		return true, 1, errors.New("the service dispatcher returned without ever calling ServiceMain")
	}
	code, ok := ctl.result()
	if !ok {
		return true, 1, errors.New("the service dispatcher returned before the beacon published an exit code")
	}
	return true, code, nil
}

// serviceMain is ServiceMain(DWORD argc, LPWSTR *argv). The SCM calls it on a
// thread of its own once the dispatcher has connected. argv[0] is always the
// service name the SCM started, which is what RegisterServiceCtrlHandlerExW
// must be given (it may differ from the name runAsService was called with if
// the service was registered under another name).
func serviceMain(argc uint32, argv **uint16) uintptr {
	scmConnected.Store(true)

	theService.mu.Lock()
	name, run := theService.name, theService.run
	theService.mu.Unlock()
	if argc > 0 && argv != nil {
		if n := utf16PtrToString(*argv); n != "" {
			name = n
		}
	}

	var handle atomic.Uintptr
	ctl := newSvcController(func(st svcStatus) error {
		return setServiceStatus(handle.Load(), st)
	})
	activeController.Store(ctl)

	namePtr, err := syscall.UTF16PtrFromString(name)
	if err == nil {
		h, _, callErr := procRegisterServiceCtrlHandlerExW.Call(uintptr(unsafe.Pointer(namePtr)), handlerCallback, 0)
		runtime.KeepAlive(namePtr)
		if h == 0 {
			err = callErr
		} else {
			handle.Store(h)
		}
	}
	if err != nil {
		// Without a status handle nothing can ever be reported, and the
		// dispatcher would wait forever for a STOPPED that never comes. Exit
		// so the SCM records a failed start and its failure actions apply.
		log.Printf("ERROR: RegisterServiceCtrlHandlerExW(%q) failed: %v — exiting", name, err)
		os.Exit(exitServiceRegisterFailed)
	}

	// serve publishes the exit code (ctl.result) before it reports STOPPED;
	// runAsService reads it from there once the dispatcher returns.
	ctl.serve(run)
	return 0
}

// serviceHandler is HandlerEx(dwControl, dwEventType, lpEventData, lpContext).
// It runs on the dispatcher thread and must return promptly; control only
// reports and cancels. NO_ERROR is returned for every control.
func serviceHandler(control, eventType, eventData, handlerContext uintptr) uintptr {
	if ctl := activeController.Load(); ctl != nil {
		ctl.control(uint32(control))
	}
	return 0
}

func setServiceStatus(h uintptr, st svcStatus) error {
	if h == 0 {
		return errors.New("no service status handle")
	}
	ws := serviceStatusW{
		ServiceType:             serviceWin32OwnProcess,
		CurrentState:            st.State,
		ControlsAccepted:        st.Accepts,
		Win32ExitCode:           st.Win32ExitCode,
		ServiceSpecificExitCode: st.SpecificExitCode,
		CheckPoint:              st.CheckPoint,
		WaitHint:                st.WaitHint,
	}
	r1, _, callErr := procSetServiceStatus.Call(h, uintptr(unsafe.Pointer(&ws)))
	if r1 == 0 {
		return callErr
	}
	return nil
}

// utf16PtrToString decodes a NUL-terminated UTF-16 string the SCM owns,
// bounded so a malformed argv cannot walk off into unmapped memory.
func utf16PtrToString(p *uint16) string {
	if p == nil {
		return ""
	}
	var s []uint16
	for i := 0; i < maxServiceNameChars; i++ {
		c := *(*uint16)(unsafe.Add(unsafe.Pointer(p), i*2))
		if c == 0 {
			break
		}
		s = append(s, c)
	}
	return string(utf16.Decode(s))
}
