package linux

import (
	"bufio"
	"bytes"
	"context"
	"net/url"
	"strings"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

// proxyKeys is the lookup order: HTTPS first, because SimCore's egress is
// HTTPS, then HTTP; the upper-case spelling first within each pair.
var proxyKeys = []string{"HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"}

// DetectProxy finds a static egress proxy: HTTPS_PROXY / HTTP_PROXY (either
// case) in env, else the same names in /etc/environment. env wins. pac is true
// when the only proxy configuration found is a PAC or WPAD one (an
// `auto_proxy=` setting or a `.pac` URL), which v1 cannot use; url is then "".
func DetectProxy(env []string, etcEnvironment []byte) (url string, pac bool) {
	envMap := map[string]string{}
	for _, kv := range env {
		if k, v, ok := strings.Cut(kv, "="); ok {
			envMap[k] = v
		}
	}
	etcMap := parseEtcEnvironment(etcEnvironment)

	for _, m := range []map[string]string{envMap, etcMap} {
		for _, k := range proxyKeys {
			v := strings.TrimSpace(m[k])
			if v == "" {
				continue
			}
			if isPACURL(v) {
				pac = true
				continue
			}
			return v, false
		}
	}
	for _, m := range []map[string]string{envMap, etcMap} {
		for _, k := range []string{"auto_proxy", "AUTO_PROXY"} {
			if strings.TrimSpace(m[k]) != "" {
				return "", true
			}
		}
	}
	return "", pac
}

func parseEtcEnvironment(b []byte) map[string]string {
	out := map[string]string{}
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		out[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"'`)
	}
	return out
}

func isPACURL(v string) bool {
	l := strings.ToLower(v)
	if i := strings.IndexAny(l, "?#"); i >= 0 {
		l = l[:i]
	}
	return strings.HasSuffix(l, ".pac") || strings.HasSuffix(l, "/wpad.dat")
}

// ProxyStep settles the egress proxy SimCore will use. Env is the process
// environment (os.Environ() in the real program); the host interface has no
// environment door, so the caller passes it in.
type ProxyStep struct {
	checkOnly
	h   host.Host
	env []string
}

func NewProxyStep(h host.Host, env []string) *ProxyStep { return &ProxyStep{h: h, env: env} }

func (*ProxyStep) ID() string    { return "proxy" }
func (*ProxyStep) Title() string { return "Proxy" }

func (s *ProxyStep) Check(_ context.Context, st *engine.State) engine.Result {
	st.Facts[FactProxy] = ""
	delete(st.Facts, FactProxyPAC)

	var raw string
	switch given := strings.TrimSpace(st.Choices.Proxy); given {
	case "none":
		return ok("No proxy")
	case "":
		etc, _ := s.h.ReadFile("/etc/environment")
		detected, pac := DetectProxy(s.env, etc)
		if pac {
			st.Facts[FactProxyPAC] = "true"
			return warn(engine.CodeProxyPACUnsupported,
				"A PAC/WPAD proxy was found; v1 cannot use it. Set a static proxy with --proxy <url>")
		}
		if detected == "" {
			return ok("None found")
		}
		raw = detected
	default:
		raw = given
	}

	if isPACURL(raw) {
		st.Facts[FactProxyPAC] = "true"
		return warn(engine.CodeProxyPACUnsupported,
			"A PAC file is not supported; set a static proxy with --proxy <url>")
	}
	if !strings.Contains(raw, "://") {
		raw = "http://" + raw
	}
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		// Do not echo the value: it may carry credentials.
		return block(engine.CodeProxyAuthUnsupported, "the proxy setting is not a valid URL; use http://host:port")
	}
	if u.User != nil {
		return block(engine.CodeProxyAuthUnsupported,
			"The proxy needs credentials, which v1 does not support; use an unauthenticated proxy or --proxy none")
	}
	st.Facts[FactProxy] = raw
	return ok("Using proxy " + u.Host)
}
