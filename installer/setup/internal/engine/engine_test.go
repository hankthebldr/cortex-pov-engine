package engine

import (
	"context"
	"reflect"
	"testing"
)

// countingStep returns fixed results and counts how often each phase ran.
type countingStep struct {
	id      string
	check   Result
	apply   Result
	onApply func()
	checks  int
	applies int
}

func (s *countingStep) ID() string    { return s.id }
func (s *countingStep) Title() string { return "step " + s.id }

func (s *countingStep) Check(context.Context, *State) Result {
	s.checks++
	return s.check
}

func (s *countingStep) Apply(context.Context, *State) Result {
	s.applies++
	if s.onApply != nil {
		s.onApply()
	}
	return s.apply
}

var pass = Result{Code: CodeOK}

func asSteps(ss ...*countingStep) []Step {
	out := make([]Step, len(ss))
	for i, s := range ss {
		out[i] = s
	}
	return out
}

// trail renders events as "<step>/<phase>/<code>" for compact comparison.
func trail(events []Event) []string {
	out := make([]string, len(events))
	for i, e := range events {
		out[i] = e.StepID + "/" + e.Phase + "/" + string(e.Result.Code)
	}
	return out
}

func TestRun_CheckModeRunsEveryCheckAndNoApply(t *testing.T) {
	a := &countingStep{id: "a", check: pass, apply: pass}
	b := &countingStep{id: "b", check: Result{Code: CodePortInUse, Detail: "port 8888 is taken"}, apply: pass}
	c := &countingStep{id: "c", check: Result{Code: CodeDiskLow, Detail: "2 GiB free"}, apply: pass}

	var emitted []Event
	rep := Run(context.Background(), asSteps(a, b, c), &State{}, ModeCheck, func(e Event) { emitted = append(emitted, e) })

	for _, s := range []*countingStep{a, b, c} {
		if s.checks != 1 {
			t.Errorf("step %s: Check ran %d times, want 1", s.id, s.checks)
		}
		if s.applies != 0 {
			t.Errorf("step %s: Apply ran %d times in check mode, want 0", s.id, s.applies)
		}
	}
	if rep.Final != CodePortInUse {
		t.Errorf("Final = %q, want the first blocking code %q", rep.Final, CodePortInUse)
	}
	want := []string{"a/check/OK", "b/check/PORT_IN_USE", "c/check/DISK_LOW"}
	if got := trail(rep.Events); !reflect.DeepEqual(got, want) {
		t.Errorf("events = %v, want %v", got, want)
	}
	if !reflect.DeepEqual(emitted, rep.Events) {
		t.Errorf("emitted %v, report holds %v; every event must be emitted once, in order", trail(emitted), trail(rep.Events))
	}
}

func TestRun_ApplyStopsAtFirstBlockingCheck(t *testing.T) {
	a := &countingStep{id: "a", check: pass, apply: pass}
	b := &countingStep{id: "b", check: Result{Code: CodeNotElevated, Detail: "run as root"}, apply: pass}
	c := &countingStep{id: "c", check: pass, apply: pass}

	rep := Run(context.Background(), asSteps(a, b, c), &State{}, ModeApply, nil)

	if a.checks != 1 || a.applies != 1 {
		t.Errorf("step a: checks=%d applies=%d, want 1 and 1", a.checks, a.applies)
	}
	if b.checks != 1 || b.applies != 0 {
		t.Errorf("step b: checks=%d applies=%d, want 1 and 0 (its Check blocked)", b.checks, b.applies)
	}
	if c.checks != 0 || c.applies != 0 {
		t.Errorf("step c: checks=%d applies=%d, want it untouched", c.checks, c.applies)
	}
	if rep.Final != CodeNotElevated {
		t.Errorf("Final = %q, want %q", rep.Final, CodeNotElevated)
	}
	want := []string{"a/check/OK", "a/apply/OK", "b/check/NOT_ELEVATED"}
	if got := trail(rep.Events); !reflect.DeepEqual(got, want) {
		t.Errorf("events = %v, want %v", got, want)
	}
}

func TestRun_WarningDoesNotBlock(t *testing.T) {
	a := &countingStep{
		id:    "a",
		check: Result{Code: CodeProxyPACUnsupported, Detail: "a PAC file is configured", Warning: true},
		apply: Result{Code: CodeEndpointProtectionExclusionNeeded, Detail: "add exclusions", Warning: true},
	}
	b := &countingStep{id: "b", check: pass, apply: pass}

	rep := Run(context.Background(), asSteps(a, b), &State{}, ModeApply, nil)

	if a.applies != 1 || b.checks != 1 || b.applies != 1 {
		t.Errorf("a.applies=%d b.checks=%d b.applies=%d, want every phase to run past a warning", a.applies, b.checks, b.applies)
	}
	if rep.Final != CodeOK {
		t.Errorf("Final = %q, want %q: a warning never decides the outcome", rep.Final, CodeOK)
	}
	want := []string{"a/check/PROXY_PAC_UNSUPPORTED", "a/apply/ENDPOINT_PROTECTION_EXCLUSION_NEEDED", "b/check/OK", "b/apply/OK"}
	if got := trail(rep.Events); !reflect.DeepEqual(got, want) {
		t.Errorf("events = %v, want %v", got, want)
	}
	if !rep.Events[0].Result.Warning {
		t.Error("the warning flag was lost on the way into the report")
	}
}

func TestRun_ApplyFailureStops(t *testing.T) {
	a := &countingStep{id: "a", check: pass, apply: pass}
	b := &countingStep{id: "b", check: pass, apply: Result{Code: CodeEngineStartFailed, Detail: "dockerd exited 1"}}
	c := &countingStep{id: "c", check: pass, apply: pass}

	rep := Run(context.Background(), asSteps(a, b, c), &State{}, ModeApply, nil)

	if c.checks != 0 || c.applies != 0 {
		t.Errorf("step c: checks=%d applies=%d, want it untouched after b's Apply failed", c.checks, c.applies)
	}
	if rep.Final != CodeEngineStartFailed {
		t.Errorf("Final = %q, want %q", rep.Final, CodeEngineStartFailed)
	}
	want := []string{"a/check/OK", "a/apply/OK", "b/check/OK", "b/apply/ENGINE_START_FAILED"}
	if got := trail(rep.Events); !reflect.DeepEqual(got, want) {
		t.Errorf("events = %v, want %v", got, want)
	}
}

// factStep records what each phase learned in State.Facts, as real steps do.
type factStep struct{}

func (factStep) ID() string    { return "facts" }
func (factStep) Title() string { return "facts" }

func (factStep) Check(_ context.Context, st *State) Result {
	st.Facts["checked"] = "yes"
	return pass
}

func (factStep) Apply(_ context.Context, st *State) Result {
	st.Facts["applied"] = "yes"
	return pass
}

func TestRun_StepsCanWriteFactsIntoAZeroState(t *testing.T) {
	for _, tc := range []struct {
		mode Mode
		want map[string]string
	}{
		{ModeCheck, map[string]string{"checked": "yes"}},
		{ModeApply, map[string]string{"checked": "yes", "applied": "yes"}},
	} {
		st := &State{}
		rep := Run(context.Background(), []Step{factStep{}}, st, tc.mode, nil)
		if rep.Final != CodeOK {
			t.Errorf("mode %d: Final = %q, want OK", tc.mode, rep.Final)
		}
		if !reflect.DeepEqual(st.Facts, tc.want) {
			t.Errorf("mode %d: Facts = %v, want %v", tc.mode, st.Facts, tc.want)
		}
	}
}

func TestRun_CancelledContextStopsBeforeTheNextPhase(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	a := &countingStep{id: "a", check: pass, apply: pass, onApply: cancel}
	b := &countingStep{id: "b", check: pass, apply: pass}

	rep := Run(ctx, asSteps(a, b), &State{}, ModeApply, nil)

	if b.checks != 0 || b.applies != 0 {
		t.Errorf("step b: checks=%d applies=%d, want nothing to start after cancellation", b.checks, b.applies)
	}
	if rep.Final != CodeCancelled {
		t.Errorf("Final = %q, want %q", rep.Final, CodeCancelled)
	}
	want := []string{"a/check/OK", "a/apply/OK", "b/check/CANCELLED"}
	if got := trail(rep.Events); !reflect.DeepEqual(got, want) {
		t.Fatalf("events = %v, want %v", got, want)
	}
	if last := rep.Events[len(rep.Events)-1].Result; last.Detail != context.Canceled.Error() {
		t.Errorf("cancel detail = %q, want %q", last.Detail, context.Canceled.Error())
	}
}
