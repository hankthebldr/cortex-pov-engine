package main

// The Windows service control manager gives a freshly started service process
// 30 seconds (ServicesPipeTimeout) to call StartServiceCtrlDispatcherW. A
// process that has not connected by then is failed with error 1053 and torn
// down. The beacon's Register call carries the 30-second HTTP client timeout,
// so a Register that runs BEFORE the dispatcher — against a SimCore that is
// briefly unreachable, which is exactly the post-reboot case — spends the whole
// budget on its own. Every elevated Windows install would then report success
// and die half a minute later, and again after every reboot.
//
// This guard reads main.go's syntax tree rather than running main, because the
// property is an ORDERING one: main must enter the dispatcher before anything
// that touches the network or the disk, and that work must live in runBeacon,
// which only runs once the dispatcher has connected (service) or declined
// (console). It is platform-neutral on purpose — the ordering is a property of
// the source, and the Linux and macOS CI jobs must catch a regression that only
// a Windows host would otherwise reveal.

import (
	"go/ast"
	"go/parser"
	"go/token"
	"testing"
)

// callName renders a call's function expression as "pkg.Func", "recv.Method"
// or "Func" so assertions can name what they are looking for.
func callName(call *ast.CallExpr) string {
	switch fn := call.Fun.(type) {
	case *ast.Ident:
		return fn.Name
	case *ast.SelectorExpr:
		if x, ok := fn.X.(*ast.Ident); ok {
			return x.Name + "." + fn.Sel.Name
		}
		return "?." + fn.Sel.Name
	}
	return ""
}

type callSite struct {
	name string
	pos  token.Pos
}

// callsIn lists every call made inside fn's body, in source order, including
// calls inside closures (a closure handed to runAsService runs under the SCM).
func callsIn(fn *ast.FuncDecl) []callSite {
	var out []callSite
	if fn == nil || fn.Body == nil {
		return out
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if call, ok := n.(*ast.CallExpr); ok {
			out = append(out, callSite{callName(call), call.Pos()})
		}
		return true
	})
	return out
}

// directCallsIn is callsIn minus anything inside a function literal: what main
// itself executes before handing control to the dispatcher or runBeacon.
func directCallsIn(fn *ast.FuncDecl) []callSite {
	var out []callSite
	if fn == nil || fn.Body == nil {
		return out
	}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		if _, ok := n.(*ast.FuncLit); ok {
			return false
		}
		if call, ok := n.(*ast.CallExpr); ok {
			out = append(out, callSite{callName(call), call.Pos()})
		}
		return true
	})
	return out
}

func parseMainFuncs(t *testing.T) map[string]*ast.FuncDecl {
	t.Helper()
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, "main.go", nil, 0)
	if err != nil {
		t.Fatalf("parse main.go: %v", err)
	}
	funcs := map[string]*ast.FuncDecl{}
	for _, d := range f.Decls {
		if fd, ok := d.(*ast.FuncDecl); ok && fd.Recv == nil {
			funcs[fd.Name.Name] = fd
		}
	}
	return funcs
}

func firstPos(calls []callSite, name string) token.Pos {
	for _, c := range calls {
		if c.name == name {
			return c.pos
		}
	}
	return token.NoPos
}

// TestMainEntersDispatcherBeforeAnyNetworkOrDiskWork pins the ordering that
// keeps an elevated Windows install alive past the SCM's 30 s budget.
func TestMainEntersDispatcherBeforeAnyNetworkOrDiskWork(t *testing.T) {
	funcs := parseMainFuncs(t)

	mainFn := funcs["main"]
	if mainFn == nil {
		t.Fatal("main.go has no func main")
	}
	// Every violation is reported, not just the first, so a regression shows
	// its whole shape in one run.
	runBeaconFn := funcs["runBeacon"]
	if runBeaconFn == nil {
		t.Error("main.go has no func runBeacon — the beacon's network/disk startup must live " +
			"in a function the service dispatcher calls AFTER it has connected")
	}

	direct := directCallsIn(mainFn)
	dispatch := firstPos(direct, "runAsService")
	if dispatch == token.NoPos {
		t.Error("main never calls runAsService — under the SCM StartServiceCtrlDispatcherW is " +
			"never reached and every service start fails with 1053 after 30 s")
	}

	// Nothing that can block on the network or walk the disk may run in main
	// before (or instead of) the dispatcher. These belong in runBeacon.
	forbidden := []string{
		"beacon.New",
		"beacon.SweepStaleStaging",
		"client.Register",
		"client.Run",
		"executor.AvailableLogicalNames",
	}
	for _, c := range callsIn(mainFn) {
		for _, bad := range forbidden {
			if c.name == bad {
				t.Errorf("main calls %s directly — it must run inside runBeacon so the "+
					"dispatcher connects first", bad)
			}
		}
	}

	// The console fallback calls runBeacon; it must come after the dispatcher
	// declined, never before it was asked.
	if rb := firstPos(direct, "runBeacon"); rb != token.NoPos && (dispatch == token.NoPos || rb < dispatch) {
		t.Error("main calls runBeacon before runAsService — the dispatcher must be entered first")
	}

	// And runBeacon must really own that work, or the move was a deletion.
	if runBeaconFn == nil {
		return
	}
	inRunBeacon := callsIn(runBeaconFn)
	for _, want := range []string{"beacon.New", "beacon.SweepStaleStaging", "client.Register", "client.Run"} {
		if firstPos(inRunBeacon, want) == token.NoPos {
			t.Errorf("runBeacon does not call %s — the beacon startup was dropped, not moved", want)
		}
	}
}

// TestSignalHandlingIsConsoleOnly: a Go process that subscribes to SIGTERM
// turns CTRL_LOGOFF_EVENT into a shutdown (see os/signal's Windows notes). A
// service receives that event whenever ANY interactive user logs off the host,
// so a service-mode beacon that called signal.Notify would stop every time a
// customer admin closed their RDP session — the JVM-without--Xrs class of bug.
// Signal handling therefore belongs to the console path only: after the
// dispatcher has declined, never in runBeacon (shared with the service path)
// and never inside a closure handed to runAsService.
func TestSignalHandlingIsConsoleOnly(t *testing.T) {
	funcs := parseMainFuncs(t)
	mainFn := funcs["main"]
	if mainFn == nil {
		t.Fatal("main.go has no func main")
	}

	if rb := funcs["runBeacon"]; rb != nil && firstPos(callsIn(rb), "signal.Notify") != token.NoPos {
		t.Error("runBeacon calls signal.Notify — the service path would subscribe to CTRL_LOGOFF_EVENT")
	}

	direct := directCallsIn(mainFn)
	directSet := map[token.Pos]bool{}
	for _, c := range direct {
		directSet[c.pos] = true
	}
	for _, c := range callsIn(mainFn) {
		if c.name == "signal.Notify" && !directSet[c.pos] {
			t.Error("signal.Notify is called inside a closure in main — closures in main run under the SCM")
		}
	}

	var lastDispatch token.Pos
	for _, c := range direct {
		if c.name == "runAsService" && c.pos > lastDispatch {
			lastDispatch = c.pos
		}
	}
	notify := firstPos(direct, "signal.Notify")
	if notify == token.NoPos {
		t.Error("main no longer installs console signal handling — Ctrl-C and SIGTERM would kill the " +
			"beacon without posting 'agent shutdown' for the in-flight step")
	} else if lastDispatch == token.NoPos || notify < lastDispatch {
		t.Error("signal.Notify runs before the service dispatcher is asked — under the SCM it would " +
			"subscribe the service to CTRL_LOGOFF_EVENT")
	}
}
