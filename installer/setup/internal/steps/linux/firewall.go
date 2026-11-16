package linux

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

// Firewall values for FactFirewall.
const (
	firewallUFW       = "ufw"
	firewallFirewalld = "firewalld"
	firewallNone      = "none"
)

// Firewall opens the chosen TCP port, from any source (D4), in the host
// firewall that is active: ufw first, else firewalld, else there is nothing to
// do. It never enables a firewall that is off. When the operator chose not to
// touch the firewall, it does nothing at all.
type Firewall struct {
	h host.Host
}

func NewFirewall(h host.Host) *Firewall { return &Firewall{h: h} }

func (*Firewall) ID() string    { return "firewall" }
func (*Firewall) Title() string { return "Firewall" }

// detect names the active host firewall. `ufw status` is translated, so it is
// read under the C locale; `firewall-cmd --state` exits 0 only when firewalld
// runs.
func (s *Firewall) detect(ctx context.Context) string {
	if _, err := s.h.LookPath("ufw"); err == nil {
		out, err := run(ctx, s.h, "env", "LC_ALL=C", "ufw", "status")
		if err == nil && strings.HasPrefix(strings.TrimSpace(string(out)), "Status: active") {
			return firewallUFW
		}
	}
	if _, err := s.h.LookPath("firewall-cmd"); err == nil {
		if _, err := run(ctx, s.h, "firewall-cmd", "--state"); err == nil {
			return firewallFirewalld
		}
	}
	return firewallNone
}

func (s *Firewall) Check(ctx context.Context, st *engine.State) engine.Result {
	if !st.Choices.Firewall {
		return ok("skipped")
	}
	fw := s.detect(ctx)
	st.Facts[FactFirewall] = fw
	if fw == firewallNone {
		return ok("no host firewall active")
	}
	return ok(fmt.Sprintf("%s is active; will allow TCP %d", fw, portOf(st)))
}

func (s *Firewall) Apply(ctx context.Context, st *engine.State) engine.Result {
	if !st.Choices.Firewall {
		return ok("skipped")
	}
	fw := s.detect(ctx)
	st.Facts[FactFirewall] = fw
	rule := strconv.Itoa(portOf(st)) + "/tcp"
	var cmds [][]string
	switch fw {
	case firewallUFW:
		cmds = [][]string{{"ufw", "allow", rule}}
	case firewallFirewalld:
		cmds = [][]string{{"firewall-cmd", "--permanent", "--add-port=" + rule}, {"firewall-cmd", "--reload"}}
	default:
		return ok("no host firewall active")
	}
	for _, c := range cmds {
		if _, stderr, err := runFor(ctx, s.h, commandTimeout, c[0], c[1:]...); err != nil {
			return block(engine.CodeFirewallRuleFailed, strings.Join(c, " ")+" failed: "+failure(stderr, err))
		}
	}
	return ok(fmt.Sprintf("%s allows TCP %s", fw, strings.TrimSuffix(rule, "/tcp")))
}
