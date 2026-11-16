package runtime

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
)

const testImage = "cortex-pov-engine-simcore:1.3.0"

// fixedPrefix is the argv every SimCore container starts with.
var fixedPrefix = []string{
	"run", "-d",
	"--name", "povengine-simcore",
	"--network", "host",
	"--restart", "unless-stopped",
	"--env-file", "/etc/povengine/simcore.env",
	"-v", "/var/lib/povengine/data:/app/data",
	// A missing image fails instead of reaching for a registry: the install
	// works offline.
	"--pull", "never",
}

func input() RunInput {
	return RunInput{
		Image:   testImage,
		Name:    ContainerName,
		EnvFile: "/etc/povengine/simcore.env",
		DataDir: "/var/lib/povengine/data",
		Address: "192.0.2.10",
		Port:    8888,
	}
}

func concat(parts ...[]string) []string {
	var out []string
	for _, p := range parts {
		out = append(out, p...)
	}
	return out
}

func TestRunArgs_Default(t *testing.T) {
	got := RunArgs(input())
	want := concat(fixedPrefix, []string{testImage})
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("argv:\n got  %q\n want %q", got, want)
	}
	// The image's own HEALTHCHECK and CMD already match the default port on
	// every interface, so nothing overrides them.
	for _, a := range got {
		if a == "--health-cmd" || a == "uvicorn" {
			t.Fatalf("default install must not override the image's health check or command: %q", got)
		}
	}
}

// The image's --host 0.0.0.0 is IPv4 only, so beacons dialling an IPv6
// address would never connect. --host :: is no better: asyncio and uvloop set
// IPV6_V6ONLY on every IPv6 listening socket, so it would refuse IPv4 (the
// image's 127.0.0.1 health check, the installer's loopback probe, IPv4
// beacons). An empty host makes them listen on 0.0.0.0 and on [::] both, so
// only the command is overridden and the 127.0.0.1 probe still answers.
func TestRunArgs_IPv6AddressListensOnEveryAddress(t *testing.T) {
	in := input()
	in.Address = "2001:db8::10"
	got := RunArgs(in)
	want := concat(fixedPrefix,
		[]string{testImage},
		[]string{"uvicorn", "main:app", "--host", "", "--port", "8888", "--workers", "1", "--timeout-graceful-shutdown", "5"},
	)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("argv:\n got  %q\n want %q", got, want)
	}

	in.Port = 9443
	got = RunArgs(in)
	want = concat(fixedPrefix,
		[]string{"--health-cmd", "curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:9443/api/health"},
		[]string{testImage},
		[]string{"uvicorn", "main:app", "--host", "", "--port", "9443", "--workers", "1", "--timeout-graceful-shutdown", "5"},
	)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("port 9443 argv:\n got  %q\n want %q", got, want)
	}
}

// An IPv4 address, or a host name, keeps the image's own command and health
// check at the default port.
func TestRunArgs_IPv4AndNamesKeepTheImageDefault(t *testing.T) {
	for _, addr := range []string{"192.0.2.10", "::ffff:192.0.2.10", "pov.example.test"} {
		in := input()
		in.Address = addr
		if got, want := RunArgs(in), concat(fixedPrefix, []string{testImage}); !reflect.DeepEqual(got, want) {
			t.Errorf("%s: argv:\n got  %q\n want %q", addr, got, want)
		}
	}
}

func TestRunArgs_EmptyNameIsTheContainerName(t *testing.T) {
	in := input()
	in.Name = ""
	if got := RunArgs(in); got[3] != "povengine-simcore" {
		t.Fatalf("--name %q, want povengine-simcore", got[3])
	}
}

func TestRunArgs_ListenOnlyOverridesHostAndHealth(t *testing.T) {
	in := input()
	in.ListenOnly = true
	got := RunArgs(in)
	want := concat(fixedPrefix,
		[]string{"--health-cmd", "curl -fsS --max-time 5 -o /dev/null http://192.0.2.10:8888/api/health"},
		[]string{testImage},
		[]string{"uvicorn", "main:app", "--host", "192.0.2.10", "--port", "8888", "--workers", "1", "--timeout-graceful-shutdown", "5"},
	)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("argv:\n got  %q\n want %q", got, want)
	}
}

func TestRunArgs_ListenOnlyIPv6IsBracketedInTheURLOnly(t *testing.T) {
	in := input()
	in.Address = "2001:db8::10"
	in.ListenOnly = true
	got := RunArgs(in)
	want := concat(fixedPrefix,
		[]string{"--health-cmd", "curl -fsS --max-time 5 -o /dev/null http://[2001:db8::10]:8888/api/health"},
		[]string{testImage},
		[]string{"uvicorn", "main:app", "--host", "2001:db8::10", "--port", "8888", "--workers", "1", "--timeout-graceful-shutdown", "5"},
	)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("argv:\n got  %q\n want %q", got, want)
	}
}

func TestRunArgs_NonDefaultPort(t *testing.T) {
	in := input()
	in.Port = 9443
	got := RunArgs(in)
	want := concat(fixedPrefix,
		[]string{"--health-cmd", "curl -fsS --max-time 5 -o /dev/null http://127.0.0.1:9443/api/health"},
		[]string{testImage},
		[]string{"uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9443", "--workers", "1", "--timeout-graceful-shutdown", "5"},
	)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("argv:\n got  %q\n want %q", got, want)
	}
}

func TestRunArgs_ZeroPortIsTheDefault(t *testing.T) {
	in := input()
	in.Port = 0
	if got, want := RunArgs(in), RunArgs(input()); !reflect.DeepEqual(got, want) {
		t.Fatalf("port 0:\n got  %q\n want %q", got, want)
	}
}

// fixture reads a /api/health body shared with the HealthGate step's tests.
func fixture(t *testing.T, name string) []byte {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "steps", "linux", "testdata", name))
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func TestJudgeHealth_ApiAuthOnlyIsOK(t *testing.T) {
	v, err := JudgeHealth(fixture(t, "health-api-auth-only.json"))
	if err != nil {
		t.Fatal(err)
	}
	if v.Code != engine.CodeOK || len(v.Faults) != 0 {
		t.Fatalf("got %+v, want OK with no faults", v)
	}
}

func TestJudgeHealth_OtherDegradedListed(t *testing.T) {
	v, err := JudgeHealth(fixture(t, "health-payload-shelf.json"))
	if err != nil {
		t.Fatal(err)
	}
	if v.Code != engine.CodeOK || !reflect.DeepEqual(v.Faults, []string{"payload_shelf"}) {
		t.Fatalf("got %+v, want OK with Faults [payload_shelf]", v)
	}
}

func TestJudgeHealth_MasterKeyMismatch(t *testing.T) {
	v, err := JudgeHealth(fixture(t, "health-master-key-mismatch.json"))
	if err != nil {
		t.Fatal(err)
	}
	if v.Code != engine.CodeMasterKeyMismatch {
		t.Fatalf("got %+v, want MASTER_KEY_MISMATCH", v)
	}
}

func TestJudgeHealth_UnknownShapeIsError(t *testing.T) {
	for name, body := range map[string]string{
		"no components":                 `{"status":"ok"}`,
		"not json":                      `<html>502 Bad Gateway</html>`,
		"empty":                         ``,
		"components not an object":      `{"components":[],"degraded_components":[]}`,
		"degraded_components missing":   `{"status":"ok","components":{"api_auth":{"status":"ok"}}}`,
		"degraded_components not array": `{"components":{},"degraded_components":"api_auth"}`,
		"null components":               `{"components":null,"degraded_components":[]}`,
	} {
		t.Run(name, func(t *testing.T) {
			v, err := JudgeHealth([]byte(body))
			if err == nil {
				t.Fatalf("got %+v and no error; an unrecognised body must never read as healthy", v)
			}
			if v.Code != engine.CodeSimCoreUnhealthy {
				t.Fatalf("code %q, want SIMCORE_UNHEALTHY", v.Code)
			}
		})
	}
}
