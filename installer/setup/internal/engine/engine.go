// Package engine is povengine-setup's step engine: the one place host work is
// ordered and judged. The CLI and the browser wizard both drive it through Run;
// neither does host work of its own.
//
// A plan is an ordered list of steps. Each step has a read-only Check, safe to
// repeat, and an Apply that does the work. Apply must be idempotent: in apply
// mode it runs after every Check that did not block, including a Check that
// found the work already done.
package engine

import "context"

// Result is what one Check or Apply reports.
type Result struct {
	Code   Code
	Detail string // one human line; never a secret
	// Warning marks a non-OK code that informs without stopping the plan
	// (for example PROXY_PAC_UNSUPPORTED).
	Warning bool
}

// Blocks reports whether this result stops an install: any code other than OK
// that is not a warning. A zero Result has no code and therefore blocks, so a
// step that forgets to say OK cannot pass silently.
func (r Result) Blocks() bool { return r.Code != CodeOK && !r.Warning }

// Choices are the operator's decisions, identical for the CLI flags and the
// wizard API.
type Choices struct {
	Address    string // the address beacons use to reach SimCore; "" = detected
	Port       int
	ListenOnly bool   // listen on Address only instead of every interface
	Firewall   bool   // open Port in the host firewall
	Proxy      string // "" = use the detected proxy, "none" = no proxy, else a URL
	Purge      bool   // uninstall only: also delete data and the master secret
}

// State is shared by every step of one run. Facts carries what earlier steps
// learned (detected addresses, versions, ids) to later ones.
type State struct {
	Version  string
	Platform string
	Choices  Choices
	Facts    map[string]string
}

// Step is one unit of host work.
type Step interface {
	ID() string    // stable, machine-readable ("ports", "engine", ...)
	Title() string // the wizard row's label
	Check(ctx context.Context, st *State) Result
	Apply(ctx context.Context, st *State) Result
}

// Mode selects what Run does. The zero value is ModeCheck, so a Mode left
// unset never changes the host.
type Mode int

const (
	// ModeCheck runs every step's Check and no Apply: the dry run.
	ModeCheck Mode = iota
	// ModeApply runs Check then Apply for each step in order, and stops at
	// the first result that blocks.
	ModeApply
)

// Event phases.
const (
	PhaseCheck = "check"
	PhaseApply = "apply"
)

// Event is one Check or Apply result, attributed to its step.
type Event struct {
	StepID string
	Phase  string // PhaseCheck or PhaseApply
	Result Result
}

// Report is the whole run: every event in order, and the outcome.
type Report struct {
	Events []Event
	// Final is the first blocking code, or OK when nothing blocked.
	Final Code
}

// Run executes steps in order.
//
// ModeCheck calls every Check, never an Apply, and keeps going past a blocking
// result so the dry run reports everything that would refuse; Final is the
// first blocking code. ModeApply calls Check then Apply for each step; a
// blocking Check stops before that step's Apply, and a blocking Apply stops the
// run. Warnings never stop either mode.
//
// Every result is passed to emit (which may be nil) as it happens and appended
// to Report.Events. Run checks ctx before starting each phase; once it is done,
// nothing further starts and the phase that would have run is reported as
// CANCELLED with ctx.Err() as its detail.
func Run(ctx context.Context, steps []Step, st *State, mode Mode, emit func(Event)) Report {
	rep := Report{Final: CodeOK}

	// record files one result and reports whether the run must stop.
	record := func(s Step, phase string, r Result) (stop bool) {
		e := Event{StepID: s.ID(), Phase: phase, Result: r}
		rep.Events = append(rep.Events, e)
		if emit != nil {
			emit(e)
		}
		if !r.Blocks() {
			return false
		}
		if rep.Final == CodeOK {
			rep.Final = r.Code
		}
		return mode == ModeApply || r.Code == CodeCancelled
	}

	// phase runs fn unless ctx is already done.
	phase := func(s Step, name string, fn func(context.Context, *State) Result) (stop bool) {
		if err := ctx.Err(); err != nil {
			record(s, name, Result{Code: CodeCancelled, Detail: err.Error()})
			return true
		}
		return record(s, name, fn(ctx, st))
	}

	for _, s := range steps {
		if phase(s, PhaseCheck, s.Check) {
			break
		}
		if mode != ModeApply {
			continue
		}
		if phase(s, PhaseApply, s.Apply) {
			break
		}
	}
	return rep
}
