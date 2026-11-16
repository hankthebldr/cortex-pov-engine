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

// AddressCandidates is addressCandidates for a single `ip -j route` document;
// kept for callers and tests that have only the IPv4 table.
func AddressCandidates(routeJSON, addrJSON []byte) ([]string, error) {
	return addressCandidates([][]byte{routeJSON}, addrJSON)
}

// excludedIfacePrefixes name container, virtualisation and overlay interfaces.
// Their addresses are never what a beacon on another host should dial; an
// operator who wants one passes --address.
var excludedIfacePrefixes = []string{"docker", "br-", "veth", "virbr", "cni", "flannel", "cali", "tailscale"}

func excludedIface(name string) bool {
	for _, p := range excludedIfacePrefixes {
		if strings.HasPrefix(name, p) {
			return true
		}
	}
	return false
}

// addressCandidates lists the addresses beacons could use to reach this host.
// routeJSONs are `ip -j [-6] route show default` documents (any family). The
// interfaces holding a default route rank first, lowest metric first (a
// multipath route contributes each nexthops[].dev); within one rank IPv4
// precedes IPv6. Loopback, link-local and container/overlay interfaces are
// never offered. A shape it does not recognise is an error, not an empty list:
// a wrong default silently hands beacons an unreachable URL.
func addressCandidates(routeJSONs [][]byte, addrJSON []byte) ([]string, error) {
	type route struct {
		Dst      string `json:"dst"`
		Dev      string `json:"dev"`
		Metric   int    `json:"metric"`
		Nexthops []struct {
			Dev string `json:"dev"`
		} `json:"nexthops"`
	}
	rank := map[string]int{} // interface -> position among default routes
	for _, rj := range routeJSONs {
		var routes []route
		if err := json.Unmarshal(rj, &routes); err != nil {
			return nil, fmt.Errorf("ip -j route: %w", err)
		}
		sort.SliceStable(routes, func(i, j int) bool { return routes[i].Metric < routes[j].Metric })
		for _, r := range routes {
			if r.Dst != "default" {
				continue
			}
			devs := []string{r.Dev}
			for _, nh := range r.Nexthops {
				devs = append(devs, nh.Dev)
			}
			for _, d := range devs {
				if d == "" || excludedIface(d) {
					continue
				}
				if _, seen := rank[d]; !seen {
					rank[d] = len(rank)
				}
			}
		}
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

	type cand struct {
		addr string
		v6   bool
		rank int // default-route position, or len(rank) for none
	}
	var cands []cand
	for _, ifc := range ifaces {
		if excludedIface(ifc.Name) {
			continue
		}
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
		if a.rank != b.rank {
			return a.rank < b.rank
		}
		return !a.v6 && b.v6 // IPv4 before IPv6 within one rank
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
	cands, candErr := s.candidates(ctx)
	st.Facts[FactAddressCandidates] = strings.Join(cands, ",")

	addr := st.Choices.Address
	if addr == "" {
		if candErr != nil {
			return block(engine.CodeAddressInvalid, "could not detect this host's address ("+candErr.Error()+"); pass --address <ip or name>")
		}
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

// candidates never fails the step by itself: an error only matters when the
// operator gave no address, and then it is shown in the block detail.
func (s *AddressStep) candidates(ctx context.Context) ([]string, error) {
	r4, err := run(ctx, s.h, "ip", "-j", "route", "show", "default")
	if err != nil {
		return nil, fmt.Errorf("ip route: %w", err)
	}
	routes := [][]byte{r4}
	// IPv6 may be disabled; its table is then simply absent.
	if r6, err := run(ctx, s.h, "ip", "-j", "-6", "route", "show", "default"); err == nil {
		routes = append(routes, r6)
	}
	addr, err := run(ctx, s.h, "ip", "-j", "addr")
	if err != nil {
		return nil, fmt.Errorf("ip addr: %w", err)
	}
	return addressCandidates(routes, addr)
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
