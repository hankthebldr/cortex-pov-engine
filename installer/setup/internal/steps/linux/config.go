package linux

import (
	"context"
	"regexp"
	"strings"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/config"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

// Config creates the master secret (or keeps the one already there) and
// writes SimCore's env file from the settled choices (§8.2, §8.3). The secret
// never leaves the two files: no Result, fact or error carries it.
type Config struct {
	h host.Host
	p config.Paths
}

func NewConfig(h host.Host, p config.Paths) *Config { return &Config{h: h, p: p} }

func (*Config) ID() string    { return "config" }
func (*Config) Title() string { return "Configuration" }

// Check reports only whether the files exist; it never reads them.
func (s *Config) Check(_ context.Context, _ *engine.State) engine.Result {
	_, secretErr := s.h.Stat(s.p.SecretFile)
	_, envErr := s.h.Stat(s.p.EnvFile)
	switch {
	case secretErr != nil:
		return ok("Will create the master secret and the env file")
	case envErr != nil:
		return ok("Will keep the master secret and write the env file")
	}
	return ok("Will keep the master secret and rewrite the env file")
}

// codePrefix matches the result code an error message starts with
// ("PROXY_AUTH_UNSUPPORTED: ...").
var codePrefix = regexp.MustCompile(`^([A-Z][A-Z0-9_]+): `)

func (s *Config) Apply(ctx context.Context, st *engine.State) engine.Result {
	secret, created, err := config.EnsureSecret(s.h, s.p)
	if err != nil {
		// EnsureSecret's errors name the file, never its content.
		return block(engine.CodeConfigWriteFailed, err.Error())
	}

	platform := st.Platform
	if platform == "" {
		platform = "linux"
	}
	var local []string
	if c := st.Facts[FactAddressCandidates]; c != "" {
		local = strings.Split(c, ",")
	}
	env, err := config.RenderEnv(config.EnvInput{
		Secret:     secret,
		Version:    st.Version,
		Platform:   platform,
		Address:    st.Choices.Address,
		Port:       portOf(st),
		ListenOnly: st.Choices.ListenOnly,
		ShortName:  s.hostname(ctx, "-s"),
		FQDN:       s.hostname(ctx, "-f"),
		Proxy:      st.Facts[FactProxy],
		LocalAddrs: local,
	})
	if err != nil {
		// RenderEnv's errors name a key or a code, never a value.
		code := engine.CodeConfigWriteFailed
		if m := codePrefix.FindStringSubmatch(err.Error()); m != nil {
			code = engine.Code(m[1])
		}
		return block(code, err.Error())
	}
	if err := s.h.MkdirAll(s.p.ConfigDir, 0o700); err != nil {
		return block(engine.CodeConfigWriteFailed, "cannot create "+s.p.ConfigDir+": "+err.Error())
	}
	if err := s.h.WriteFile(s.p.EnvFile, env, 0o600); err != nil {
		return block(engine.CodeConfigWriteFailed, "cannot write "+s.p.EnvFile+": "+err.Error())
	}
	if created {
		return ok("Master secret created; env file written")
	}
	return ok("Master secret kept; env file written")
}

// hostname returns `hostname <flag>`, "" when it fails: a name that cannot be
// read is simply not added to the allowed hosts (an IP address always is).
func (s *Config) hostname(ctx context.Context, flag string) string {
	out, err := run(ctx, s.h, "hostname", flag)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}
