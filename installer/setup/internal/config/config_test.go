package config

import (
	"reflect"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

func TestDefaultPaths_Linux(t *testing.T) {
	want := Paths{
		Program:    "/opt/povengine",
		ConfigDir:  "/etc/povengine",
		SecretFile: "/etc/povengine/secret",
		EnvFile:    "/etc/povengine/simcore.env",
		DataDir:    "/var/lib/povengine/data",
		EngineData: "/var/lib/povengine/engine",
		Backups:    "/var/lib/povengine/backups",
		Logs:       "/var/log/povengine",
		StateFile:  "/etc/povengine/setup-state.json",
	}
	if got := DefaultPaths("linux"); got != want {
		t.Fatalf("linux paths = %+v, want %+v", got, want)
	}
}

func TestDefaultPaths_Windows(t *testing.T) {
	want := Paths{
		Program:    `C:\Program Files\POVengine`,
		ConfigDir:  `C:\ProgramData\POVengine\config`,
		SecretFile: `C:\ProgramData\POVengine\config\secret`,
		EnvFile:    `C:\ProgramData\POVengine\config\simcore.env`,
		DataDir:    "/var/lib/povengine/data",
		EngineData: "/var/lib/povengine/engine",
		Backups:    `C:\ProgramData\POVengine\backups`,
		Logs:       `C:\ProgramData\POVengine\logs`,
		StateFile:  `C:\ProgramData\POVengine\config\setup-state.json`,
	}
	if got := DefaultPaths("windows"); got != want {
		t.Fatalf("windows paths = %+v, want %+v", got, want)
	}
}

func TestGenerateSecret_LengthAndAlphabet(t *testing.T) {
	a, err := GenerateSecret()
	if err != nil {
		t.Fatal(err)
	}
	b, _ := GenerateSecret()
	if len(a) != 64 || !validSecret(a) {
		t.Fatalf("bad secret %q", a)
	}
	if a == b {
		t.Fatal("two secrets are equal")
	}
}

func goodSecret(t *testing.T) string {
	t.Helper()
	s, err := GenerateSecret()
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func countWrites(f *host.Fake) int {
	n := 0
	for _, c := range f.Calls {
		if c.Method == "WriteFile" {
			n++
		}
	}
	return n
}

func TestEnsureSecret_KeepsExisting(t *testing.T) {
	p := DefaultPaths("linux")
	s := goodSecret(t)
	f := &host.Fake{Files: map[string][]byte{p.SecretFile: []byte(s + "\n")}}
	got, created, err := EnsureSecret(f, p)
	if err != nil || created || got != s {
		t.Fatalf("got %q created=%v err=%v", got, created, err)
	}
	if countWrites(f) != 0 {
		t.Fatal("existing secret was rewritten")
	}
}

func TestEnsureSecret_WritesOwnerOnly(t *testing.T) {
	p := DefaultPaths("linux")
	f := &host.Fake{}
	got, created, err := EnsureSecret(f, p)
	if err != nil || !created || !validSecret(got) {
		t.Fatalf("got %q created=%v err=%v", got, created, err)
	}
	var wrote, mkdir bool
	for _, c := range f.Calls {
		if c.Method == "WriteFile" && c.Args[0] == p.SecretFile {
			wrote = true
			if c.Mode != 0o600 {
				t.Fatalf("secret mode %o, want 600", c.Mode)
			}
		}
		if c.Method == "MkdirAll" && c.Args[0] == p.ConfigDir {
			mkdir = true
			if c.Mode != 0o700 {
				t.Fatalf("config dir mode %o, want 700", c.Mode)
			}
		}
	}
	if !wrote || !mkdir {
		t.Fatalf("wrote=%v mkdir=%v", wrote, mkdir)
	}
	again, created2, err := EnsureSecret(f, p)
	if err != nil || created2 || again != got {
		t.Fatalf("second call: %q created=%v err=%v", again, created2, err)
	}
}

func TestEnsureSecret_InvalidExistingIsAnErrorAndUntouched(t *testing.T) {
	p := DefaultPaths("linux")
	bad := map[string]string{
		"empty":     "",
		"short":     "abc",
		"long":      strings.Repeat("a", 65),
		"bad chars": strings.Repeat("a", 63) + "+",
	}
	for name, content := range bad {
		t.Run(name, func(t *testing.T) {
			f := &host.Fake{Files: map[string][]byte{p.SecretFile: []byte(content)}}
			_, _, err := EnsureSecret(f, p)
			if err == nil {
				t.Fatal("want error")
			}
			if content != "" && strings.Contains(err.Error(), content) {
				t.Fatal("error leaks file content")
			}
			if countWrites(f) != 0 || string(f.Files[p.SecretFile]) != content {
				t.Fatal("invalid secret was replaced")
			}
		})
	}
}

func envMap(t *testing.T, b []byte) map[string]string {
	t.Helper()
	m := map[string]string{}
	for _, l := range strings.Split(strings.TrimSuffix(string(b), "\n"), "\n") {
		k, v, ok := strings.Cut(l, "=")
		if !ok {
			t.Fatalf("line without =: %q", l)
		}
		m[k] = v
	}
	return m
}

func render(t *testing.T, in EnvInput) map[string]string {
	t.Helper()
	b, err := RenderEnv(in)
	if err != nil {
		t.Fatal(err)
	}
	return envMap(t, b)
}

func base() EnvInput {
	return EnvInput{Secret: strings.Repeat("s", 64), Version: "1.3.0", Platform: "linux", Address: "192.0.2.10", Port: 8888, ShortName: "jumpbox01"}
}

func TestRenderEnv_HostNames(t *testing.T) {
	cases := []struct {
		name string
		mod  func(*EnvInput)
		want string
	}{
		{"upper short only", func(i *EnvInput) { i.ShortName = "JUMPBOX01" }, "jumpbox01"},
		{"short fqdn dns address", func(i *EnvInput) {
			i.FQDN = "jumpbox01.corp.example"
			i.Address = "pov.corp.example"
		}, "jumpbox01,jumpbox01.corp.example,pov.corp.example"},
		{"ip address excluded", func(i *EnvInput) { i.FQDN = "jumpbox01.corp.example" }, "jumpbox01,jumpbox01.corp.example"},
		{"ipv6 address excluded", func(i *EnvInput) { i.Address = "2001:db8::10" }, "jumpbox01"},
		{"duplicates and empties", func(i *EnvInput) {
			i.ShortName = "Jumpbox01"
			i.FQDN = "JUMPBOX01"
			i.Address = "jumpbox01"
		}, "jumpbox01"},
		{"empty short", func(i *EnvInput) {
			i.ShortName = ""
			i.FQDN = "jumpbox01.corp.example"
		}, "jumpbox01.corp.example"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			in := base()
			c.mod(&in)
			got := render(t, in)["CORTEXSIM_ALLOWED_HOSTS"]
			if got != c.want {
				t.Fatalf("got %q want %q", got, c.want)
			}
			if strings.HasPrefix(got, ",") || strings.HasSuffix(got, ",") || strings.Contains(got, ",,") {
				t.Fatalf("malformed list %q", got)
			}
		})
	}
}

func TestRenderEnv_PublicURLAndInstallVars(t *testing.T) {
	m := render(t, base())
	want := map[string]string{
		"CORTEXSIM_PUBLIC_URLS":     "http://192.0.2.10:8888",
		"CORTEXSIM_INSTALL_ADDRESS": "192.0.2.10:8888",
		"CORTEXSIM_INSTALLED_BY":    "povengine-setup/1.3.0+linux",
		"CORTEXSIM_ENV":             "production",
		"CORTEXSIM_SECRET":          strings.Repeat("s", 64),
	}
	for k, v := range want {
		if m[k] != v {
			t.Errorf("%s = %q, want %q", k, m[k], v)
		}
	}
}

func TestRenderEnv_IPv6AddressIsBracketed(t *testing.T) {
	in := base()
	in.Address = "2001:db8::10"
	m := render(t, in)
	if m["CORTEXSIM_PUBLIC_URLS"] != "http://[2001:db8::10]:8888" || m["CORTEXSIM_INSTALL_ADDRESS"] != "[2001:db8::10]:8888" {
		t.Fatalf("got %q %q", m["CORTEXSIM_PUBLIC_URLS"], m["CORTEXSIM_INSTALL_ADDRESS"])
	}
}

func TestRenderEnv_BindOnlyWhenListenOnly(t *testing.T) {
	if _, ok := render(t, base())["CORTEXSIM_BIND"]; ok {
		t.Fatal("CORTEXSIM_BIND set without ListenOnly")
	}
	in := base()
	in.ListenOnly = true
	if got := render(t, in)["CORTEXSIM_BIND"]; got != "192.0.2.10" {
		t.Fatalf("bind = %q", got)
	}
}

func TestRenderEnv_ProxyAndNoProxy(t *testing.T) {
	in := base()
	in.Proxy = "http://198.51.100.7:3128"
	in.LocalAddrs = []string{"192.0.2.10", "198.51.100.20", "Jumpbox01"}
	m := render(t, in)
	if m["HTTPS_PROXY"] != in.Proxy || m["HTTP_PROXY"] != in.Proxy {
		t.Fatalf("proxy keys: %v", m)
	}
	if want := "localhost,127.0.0.1,::1,jumpbox01,192.0.2.10,198.51.100.20"; m["NO_PROXY"] != want {
		t.Fatalf("NO_PROXY = %q, want %q", m["NO_PROXY"], want)
	}
	for _, p := range []string{"none", "NONE", "", "  "} {
		in.Proxy = p
		m := render(t, in)
		for _, k := range []string{"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"} {
			if _, ok := m[k]; ok {
				t.Fatalf("proxy %q still wrote %s", p, k)
			}
		}
	}
}

func TestRenderEnv_KeyOrderIsStable(t *testing.T) {
	in := base()
	in.ListenOnly = true
	in.Proxy = "http://198.51.100.7:3128"
	b, err := RenderEnv(in)
	if err != nil {
		t.Fatal(err)
	}
	var keys []string
	for _, l := range strings.Split(strings.TrimSuffix(string(b), "\n"), "\n") {
		k, _, _ := strings.Cut(l, "=")
		keys = append(keys, k)
	}
	if !reflect.DeepEqual(keys, EnvKeyOrder) {
		t.Fatalf("order %v, want %v", keys, EnvKeyOrder)
	}
	b2, _ := RenderEnv(in)
	if string(b) != string(b2) {
		t.Fatal("not deterministic")
	}
	if strings.Contains(string(b), "\r") || strings.Contains(string(b), "\n\n") || strings.Contains(string(b), `"`) {
		t.Fatal("format: CR, blank line or quote present")
	}
}

func TestRenderEnv_RefusesNewlineAndNUL(t *testing.T) {
	for _, bad := range []string{"a\nb", "a\rb", "a\x00b"} {
		in := base()
		in.Secret = bad
		if _, err := RenderEnv(in); err == nil {
			t.Fatalf("value %q accepted", bad)
		}
		in = base()
		in.Proxy = "http://198.51.100.7:3128/" + bad
		if _, err := RenderEnv(in); err == nil {
			t.Fatalf("proxy %q accepted", bad)
		}
	}
}

func TestPublicLines_NeverContainsSecret(t *testing.T) {
	in := base()
	in.Secret = "TOPSECRETVALUE"
	in.Proxy = "http://alice:p%40ss@198.51.100.7:3128"
	b, err := RenderEnv(in)
	if err != nil {
		t.Fatal(err)
	}
	lines := PublicLines(b)
	joined := strings.Join(lines, "\n")
	if strings.Contains(joined, "TOPSECRETVALUE") || strings.Contains(joined, "CORTEXSIM_SECRET") {
		t.Fatal("secret in public lines")
	}
	if strings.Contains(joined, "alice") || strings.Contains(joined, "p%40ss") {
		t.Fatalf("proxy credentials leaked: %s", joined)
	}
	found := false
	for _, l := range lines {
		if l == "HTTPS_PROXY=http://***@198.51.100.7:3128" {
			found = true
		}
	}
	if !found {
		t.Fatalf("masked proxy line missing: %v", lines)
	}
	if len(lines) != len(strings.Split(strings.TrimSuffix(string(b), "\n"), "\n"))-1 {
		t.Fatalf("expected every line but the secret: %v", lines)
	}
}
