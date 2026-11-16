package linux

import (
	"context"
	"encoding/json"
	"fmt"
	"net/netip"
	"sort"
	"strings"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

// AddressCandidates lists the addresses beacons could use to reach this host,
// from `ip -j route` and `ip -j addr`: the IPv4 addresses of the interface
// holding the default route first (lowest metric first), then every other
// global IPv4, then global IPv6. Loopback and link-local addresses are never
// offered. A shape it does not recognise is an error, not an empty list: a
// wrong default silently hands beacons an unreachable URL.
func AddressCandidates(routeJSON, addrJSON []byte) ([]string, error) {
	var routes []struct {
		Dst    string `json:"dst"`
		Dev    string `json:"dev"`
		Metric int    `json:"metric"`
	}
	if err := json.Unmarshal(routeJSON, &routes); err != nil {
		return nil, fmt.Errorf("ip -j route: %w", err)
	}
	var ifaces []struct {
		Name  string `json:"ifname"`
		Addrs []struct {
			Family string `json:"family"`
			Local  string `json:"local"`
			Scope  string `json:"scope"`
		} `json:"addr_info"`
	}
	if err := json.Unmarshal(addrJSON, &ifaces); err != nil {
		return nil, fmt.Errorf("ip -j addr: %w", err)
	}

	sort.SliceStable(routes, func(i, j int) bool { return routes[i].Metric < routes[j].Metric })
	rank := map[string]int{} // interface -> position among default routes
	for _, r := range routes {
		if r.Dst != "default" || r.Dev == "" {
			continue
		}
		if _, seen := rank[r.Dev]; !seen {
			rank[r.Dev] = len(rank)
		}
	}

	type cand struct {
		addr string
		v6   bool
		rank int // default-route position, or len(rank) for none
	}
	var cands []cand
	for _, ifc := range ifaces {
		rk, isDefault := rank[ifc.Name]
		if !isDefault {
			rk = len(rank)
		}
		for _, a := range ifc.Addrs {
			if a.Scope != "global" || (a.Family != "inet" && a.Family != "inet6") {
				continue
			}
			ip, err := netip.ParseAddr(a.Local)
			if err != nil || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified() || ip.IsMulticast() {
				continue
			}
			cands = append(cands, cand{addr: ip.String(), v6: a.Family == "inet6", rank: rk})
		}
	}
	sort.SliceStable(cands, func(i, j int) bool {
		a, b := cands[i], cands[j]
		if a.v6 != b.v6 {
			return !a.v6 // IPv4 before IPv6
		}
		return a.rank < b.rank
	})
	seen := map[string]bool{}
	out := []string{}
	for _, c := range cands {
		if !seen[c.addr] {
			seen[c.addr] = true
			out = append(out, c.addr)
		}
	}
	return out, nil
}

// AddressStep settles the address beacons use to reach SimCore: it defaults an
// empty choice to the first candidate and validates one the operator gave.
type AddressStep struct {
	checkOnly
	h host.Host
}

func NewAddressStep(h host.Host) *AddressStep { return &AddressStep{h: h} }

func (*AddressStep) ID() string    { return "address" }
func (*AddressStep) Title() string { return "Address" }

func (s *AddressStep) Check(ctx context.Context, st *engine.State) engine.Result {
	cands := s.candidates(ctx)
	st.Facts[FactAddressCandidates] = strings.Join(cands, ",")

	addr := st.Choices.Address
	if addr == "" {
		if len(cands) == 0 {
			return block(engine.CodeAddressInvalid, "no usable address found on this host; pass --address <ip or name>")
		}
		st.Choices.Address = cands[0]
		return ok("Beacons will reach this host at " + cands[0])
	}

	isIP, valid := classifyAddress(addr)
	if !valid {
		return block(engine.CodeAddressInvalid,
			"the address must be an IP address or a host name, with no scheme, port or spaces")
	}
	if st.Choices.ListenOnly && !isIP {
		return block(engine.CodeAddressInvalid, "listen-only binds one address, so it needs an IP address, not a name")
	}
	return ok("Beacons will reach this host at " + addr)
}

// candidates never fails the step: a missing or unreadable `ip` only means
// nothing is detected, which matters only when the operator gave no address.
func (s *AddressStep) candidates(ctx context.Context) []string {
	route, err := run(ctx, s.h, "ip", "-j", "route")
	if err != nil {
		return nil
	}
	addr, err := run(ctx, s.h, "ip", "-j", "addr")
	if err != nil {
		return nil
	}
	c, err := AddressCandidates(route, addr)
	if err != nil {
		return nil
	}
	return c
}

// classifyAddress reports whether a is an IP literal (isIP) and whether it is
// acceptable at all: an IP literal without brackets or zone, or an RFC 1123
// host name.
func classifyAddress(a string) (isIP, valid bool) {
	if ip, err := netip.ParseAddr(a); err == nil {
		return true, ip.Zone() == ""
	}
	return false, validHostName(a)
}

func validHostName(h string) bool {
	if h == "" || len(h) > 253 {
		return false
	}
	labels := strings.Split(h, ".")
	for _, l := range labels {
		if l == "" || len(l) > 63 || l[0] == '-' || l[len(l)-1] == '-' {
			return false
		}
		for i := 0; i < len(l); i++ {
			c := l[i]
			if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-') {
				return false
			}
		}
	}
	// An all-numeric last label is a malformed IP address, never a host name.
	last := labels[len(labels)-1]
	allDigits := true
	for i := 0; i < len(last); i++ {
		if last[i] < '0' || last[i] > '9' {
			allDigits = false
			break
		}
	}
	return !allDigits
}
