package beacon

import (
	"testing"
	"time"
)

// stepTimeout is the only place three sources of truth are reconciled, and
// getting the order wrong is silent: the run still completes, just with the
// wrong budget. Before --step-timeout existed, c.StepTimeout was unreachable
// from the CLI so the middle arm was dead code.
func TestStepTimeout_PrecedenceChain(t *testing.T) {
	cases := []struct {
		name        string
		clientLimit time.Duration
		stepSeconds int
		want        time.Duration
	}{
		{"nothing set falls back to the built-in default", 0, 0, defaultStepTimeout},
		{"--step-timeout overrides the built-in default", 45 * time.Second, 0, 45 * time.Second},
		{"a step's own timeout_seconds beats --step-timeout", 45 * time.Second, 5, 5 * time.Second},
		{"a step's own timeout_seconds beats the default", 0, 7, 7 * time.Second},
		{"a zero/absent step value does not zero the budget", 30 * time.Second, 0, 30 * time.Second},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := &BeaconClient{StepTimeout: tc.clientLimit}
			got := c.stepTimeout(&Step{TimeoutSeconds: tc.stepSeconds})
			if got != tc.want {
				t.Errorf("stepTimeout() = %v, want %v", got, tc.want)
			}
		})
	}
}

// A nil step must not panic and must not silently yield a zero budget — a
// zero-duration context deadline would terminate every step instantly.
func TestStepTimeout_NilStepUsesTheConfiguredBudget(t *testing.T) {
	c := &BeaconClient{StepTimeout: 20 * time.Second}
	if got := c.stepTimeout(nil); got != 20*time.Second {
		t.Errorf("stepTimeout(nil) = %v, want 20s", got)
	}
	empty := &BeaconClient{}
	if got := empty.stepTimeout(nil); got != defaultStepTimeout {
		t.Errorf("stepTimeout(nil) with no override = %v, want %v", got, defaultStepTimeout)
	}
}
