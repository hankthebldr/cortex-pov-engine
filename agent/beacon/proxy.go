package beacon

// How the beacon reaches SimCore: directly, through a proxy named on the
// command line, or through the HTTP(S)_PROXY environment (BEACON-19).
//
// WHAT THIS DOES NOT DO, stated rather than implied: it does not read the
// Windows system proxy — the per-user WinINET settings (including a PAC file or
// WPAD) or the machine-wide WinHTTP proxy. Go's net/http reads the environment
// only. Reading the WinHTTP static proxy alone would be a few winhttp.dll calls,
// but the configuration enterprises actually use is a per-user PAC file, which a
// LocalSystem service does not have and which needs a script engine to
// evaluate; a partial reader would look like system-proxy support without
// being it, and it would be Windows-only syscall code on the beacon's startup
// path that nothing in this repo can execute. So the beacon takes an explicit
// --proxy instead, and says at startup — in the log a DC reads on the endpoint
// — which route it is using and that the system proxy was not consulted.
//
// Why it matters on Windows in particular: the installer downloads and enrolls
// with Invoke-WebRequest / Invoke-RestMethod, which DO honour the system proxy.
// An install can therefore succeed through a proxy the beacon then never uses,
// and the agent simply never appears.

import (
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

// ProxyDirect is the --proxy value that forces a direct connection and ignores
// HTTP(S)_PROXY — for the machine-wide proxy variable that would otherwise send
// lab-internal SimCore traffic to a corporate proxy.
const ProxyDirect = "direct"

// ParseProxySpec validates a --proxy value. "" means "use the environment",
// ProxyDirect means "never use a proxy"; anything else must be an absolute
// http, https or socks5 URL naming a host.
func ParseProxySpec(spec string) (proxyURL *url.URL, direct bool, err error) {
	spec = strings.TrimSpace(spec)
	switch {
	case spec == "":
		return nil, false, nil
	case strings.EqualFold(spec, ProxyDirect):
		return nil, true, nil
	}
	// No error below quotes the value: a proxy URL can carry credentials, and
	// these messages reach the console and the service log.
	u, perr := url.Parse(spec)
	if perr != nil {
		var ue *url.Error
		if errors.As(perr, &ue) {
			perr = ue.Err // url.Error's own text repeats the whole URL
		}
		return nil, false, fmt.Errorf("not a URL (%v)", perr)
	}
	switch u.Scheme {
	case "http", "https", "socks5":
	default:
		return nil, false, fmt.Errorf("scheme %q is not supported (http, https or socks5 — e.g. http://proxy.corp.example:8080)", u.Scheme)
	}
	if u.Hostname() == "" {
		return nil, false, errors.New("the URL names no proxy host")
	}
	return u, false, nil
}

// SetProxy applies a --proxy value to BOTH of the beacon's HTTP clients (the
// SimCore API client and the artifact-fetch client). An empty spec changes
// nothing: the clients keep Go's default, which is the environment.
func (c *BeaconClient) SetProxy(spec string) error {
	u, direct, err := ParseProxySpec(spec)
	if err != nil {
		return err
	}
	if u == nil && !direct {
		return nil
	}
	var t *http.Transport
	if base, ok := http.DefaultTransport.(*http.Transport); ok {
		t = base.Clone()
	} else {
		t = &http.Transport{}
	}
	if direct {
		t.Proxy = nil
	} else {
		t.Proxy = http.ProxyURL(u)
	}
	c.proxyTransport, c.proxyURL, c.proxyDirect = t, u, direct
	if c.http == nil {
		c.http = &http.Client{}
	}
	c.http.Transport = t
	if c.artifactHTTP != nil {
		c.artifactHTTP.Transport = t
	}
	return nil
}

// proxyFromEnvironment is http.ProxyFromEnvironment. A var only so a test can
// answer for the environment (net/http reads it once per process).
var proxyFromEnvironment = http.ProxyFromEnvironment

// ProxyStartupLines returns the lines the beacon logs at startup about its
// route to SimCore: always which route, and — on Windows, when nobody chose one
// — that the system proxy is not read.
func (c *BeaconClient) ProxyStartupLines(goos string) []string {
	switch {
	case c.proxyDirect:
		return []string{fmt.Sprintf("proxy: SimCore (%s) is reached directly (--proxy %s); HTTP(S)_PROXY is ignored",
			c.ServerURL, ProxyDirect)}
	case c.proxyURL != nil:
		return []string{fmt.Sprintf("proxy: SimCore (%s) is reached through %s (--proxy / CORTEXSIM_AGENT_PROXY); "+
			"HTTP(S)_PROXY and NO_PROXY are ignored", c.ServerURL, c.proxyURL.Redacted())}
	}

	var lines []string
	req, err := http.NewRequest(http.MethodGet, c.ServerURL+"/api/agents/"+c.AgentID+"/tasks", nil)
	if err != nil {
		lines = append(lines, fmt.Sprintf("proxy: cannot evaluate the route to SimCore (%s): %v", c.ServerURL, err))
	} else if u, perr := proxyFromEnvironment(req); perr != nil {
		lines = append(lines, fmt.Sprintf("proxy: the HTTP(S)_PROXY environment is invalid (%v) — requests to "+
			"SimCore (%s) will fail until it is fixed, or pass --proxy", perr, c.ServerURL))
	} else if u != nil {
		lines = append(lines, fmt.Sprintf("proxy: SimCore (%s) is reached through %s (from the HTTP_PROXY / "+
			"HTTPS_PROXY environment; NO_PROXY applies)", c.ServerURL, u.Redacted()))
	} else {
		lines = append(lines, fmt.Sprintf("proxy: SimCore (%s) is reached directly (no --proxy, and no "+
			"HTTP(S)_PROXY applies to it)", c.ServerURL))
	}
	if goos == "windows" {
		lines = append(lines, "proxy: the Windows system proxy (WinINET / WinHTTP settings, PAC, WPAD) is NOT read "+
			"by this beacon — only --proxy and HTTP(S)_PROXY / NO_PROXY are. The installer's Invoke-WebRequest "+
			"does read it, so an install that worked through a system proxy can be followed by a beacon that "+
			"cannot reach SimCore. If this host needs a proxy for SimCore, put --proxy <url> on the beacon's "+
			"command line; if a machine-wide HTTP_PROXY must not apply, --proxy "+ProxyDirect+".")
	}
	return lines
}
