package linux

import (
	"context"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

// EndpointProtection spots a Cortex XDR agent, which may quarantine the
// payload or block the engine (R5), and names the exclusion paths. It never
// changes protection settings.
type EndpointProtection struct {
	checkOnly
	h host.Host
}

func NewEndpointProtection(h host.Host) *EndpointProtection { return &EndpointProtection{h: h} }

func (*EndpointProtection) ID() string    { return "protection" }
func (*EndpointProtection) Title() string { return "Endpoint protection" }

func (s *EndpointProtection) Check(ctx context.Context, st *engine.State) engine.Result {
	found := false
	if _, err := s.h.Stat("/opt/traps"); err == nil {
		found = true
	} else if _, err := run(ctx, s.h, "pgrep", "-x", "traps_pmd"); err == nil {
		// pgrep exits 0 only on a match; a missing pgrep or no match is an error.
		found = true
	}
	if !found {
		st.Facts[FactEndpointProtection] = "none"
		return ok("None found")
	}
	st.Facts[FactEndpointProtection] = "cortex-xdr"
	return warn(engine.CodeEndpointProtectionExclusionNeeded,
		"Cortex XDR found; exclude /opt/povengine and /var/lib/povengine")
}
