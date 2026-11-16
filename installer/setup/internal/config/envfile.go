package config

import (
	"fmt"
	"net"
	"strconv"
	"strings"
)

// EnvInput is everything the env file is built from.
type EnvInput struct {
	Secret, Version, Platform, Address string
	Port                               int
	ListenOnly                         bool
	ShortName, FQDN                    string
	// Proxy is an http(s) proxy URL. "" and "none" mean no proxy.
	Proxy      string
	LocalAddrs []string
}

// EnvKeyOrder is the order RenderEnv writes keys in; a key with no value for
// this input is skipped.
var EnvKeyOrder = []string{
	"CORTEXSIM_ENV",
	"CORTEXSIM_SECRET",
	"CORTEXSIM_ALLOWED_HOSTS",
	"CORTEXSIM_PUBLIC_URLS",
	"CORTEXSIM_INSTALLED_BY",
	"CORTEXSIM_INSTALL_ADDRESS",
	"CORTEXSIM_BIND",
	"HTTPS_PROXY",
	"HTTP_PROXY",
	"NO_PROXY",
}

// RenderEnv returns the `docker run --env-file` text: KEY=VALUE per line, LF
// endings, no quotes (Docker does not unquote). It fails when a value holds a
// CR, LF or NUL, which the format cannot carry.
func RenderEnv(in EnvInput) ([]byte, error) {
	hostPort := net.JoinHostPort(in.Address, strconv.Itoa(in.Port))
	vals := map[string]string{
		"CORTEXSIM_ENV":             "production",
		"CORTEXSIM_SECRET":          in.Secret,
		"CORTEXSIM_ALLOWED_HOSTS":   strings.Join(allowedHosts(in), ","),
		"CORTEXSIM_PUBLIC_URLS":     "http://" + hostPort,
		"CORTEXSIM_INSTALLED_BY":    "povengine-setup/" + in.Version + "+" + in.Platform,
		"CORTEXSIM_INSTALL_ADDRESS": hostPort,
	}
	if in.ListenOnly {
		vals["CORTEXSIM_BIND"] = in.Address
	}
	if p := strings.TrimSpace(in.Proxy); p != "" && !strings.EqualFold(p, "none") {
		vals["HTTPS_PROXY"] = p
		vals["HTTP_PROXY"] = p
		vals["NO_PROXY"] = strings.Join(noProxy(in), ",")
	}
	var b strings.Builder
	for _, k := range EnvKeyOrder {
		v, ok := vals[k]
		if !ok || v == "" {
			continue
		}
		if strings.ContainsAny(v, "\r\n\x00") {
			return nil, fmt.Errorf("env value for %s contains a newline or NUL, which an env file cannot carry", k)
		}
		b.WriteString(k + "=" + v + "\n")
	}
	return []byte(b.String()), nil
}

// normList lower-cases, trims, drops empties and removes duplicates, keeping
// first-seen order.
func normList(in ...string) []string {
	seen := map[string]bool{}
	var out []string
	for _, s := range in {
		s = strings.ToLower(strings.TrimSpace(s))
		if s == "" || seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

func allowedHosts(in EnvInput) []string {
	list := []string{in.ShortName, in.FQDN}
	if net.ParseIP(strings.Trim(strings.TrimSpace(in.Address), "[]")) == nil {
		list = append(list, in.Address)
	}
	return normList(list...)
}

func noProxy(in EnvInput) []string {
	list := []string{"localhost", "127.0.0.1", "::1", in.ShortName, in.FQDN, in.Address}
	list = append(list, in.LocalAddrs...)
	return normList(list...)
}

// PublicLines returns the env file's lines that are safe to show: every line
// except CORTEXSIM_SECRET, with credentials in a proxy URL replaced by ***.
func PublicLines(env []byte) []string {
	var out []string
	for _, line := range strings.Split(string(env), "\n") {
		line = strings.TrimSuffix(line, "\r")
		if line == "" || strings.HasPrefix(line, "CORTEXSIM_SECRET=") {
			continue
		}
		out = append(out, maskCredentials(line))
	}
	return out
}

// maskCredentials turns scheme://user:pass@host into scheme://***@host.
func maskCredentials(line string) string {
	i := strings.Index(line, "://")
	if i < 0 {
		return line
	}
	rest := line[i+3:]
	end := strings.IndexAny(rest, "/?#")
	if end < 0 {
		end = len(rest)
	}
	at := strings.LastIndex(rest[:end], "@")
	if at < 0 {
		return line
	}
	return line[:i+3] + "***" + rest[at:]
}
