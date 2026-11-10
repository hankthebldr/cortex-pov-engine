package runtimefacts

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"testing"
)

func envFrom(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}

func lookPathIn(present ...string) func(string) (string, error) {
	set := map[string]bool{}
	for _, p := range present {
		set[p] = true
	}
	return func(n string) (string, error) {
		if set[n] {
			return "/usr/bin/" + n, nil
		}
		return "", errors.New("not found")
	}
}

func readFileFrom(files map[string]string) func(string) ([]byte, error) {
	return func(p string) ([]byte, error) {
		if v, ok := files[p]; ok {
			return []byte(v), nil
		}
		return nil, os.ErrNotExist
	}
}

func lookupUserIn(present ...string) func(string) error {
	set := map[string]bool{}
	for _, p := range present {
		set[p] = true
	}
	return func(n string) error {
		if set[n] {
			return nil
		}
		return errors.New("unknown user")
	}
}

func TestCollect(t *testing.T) {
	inCluster := map[string]string{
		"KUBERNETES_SERVICE_HOST": "10.43.0.1",
		"POD_NAME":                "cortexsim-agent-abc",
		"POD_NAMESPACE":           "cortexsim-agent",
		"NODE_NAME":               "worker-1",
	}
	cases := []struct {
		name string
		opts Options
		want Facts
	}{
		{
			name: "in-cluster pod with tools, accounts and failed prepare",
			opts: Options{
				PostureProfile:    "baseline",
				PrepareStatusFile: "/cortexsim/prepare.status",
				ProbeTools:        []string{"wget", "curl", "nmap", "jq"},
				ProbeAccounts:     []string{"www-data", "postgres", "ghost"},
				Getenv:            envFrom(inCluster),
				LookPath:          lookPathIn("curl", "jq", "wget", "bash"),
				ReadFile: readFileFrom(map[string]string{
					"/cortexsim/prepare.status": "PREPARE_FAILED: apt-get update timed out\nsecond line\n",
				}),
				LookupUser: lookupUserIn("www-data", "postgres"),
			},
			want: Facts{
				Container: true, PodName: "cortexsim-agent-abc", PodNamespace: "cortexsim-agent",
				NodeName: "worker-1", PostureProfile: "baseline",
				PrepareStatus: "PREPARE_FAILED: apt-get update timed out",
				Tools:         []string{"curl", "jq", "wget"},
				Accounts:      []string{"postgres", "www-data"},
				AnchorRename:  true,
			},
		},
		{
			name: "bare host: not a container, missing status file, no bash",
			opts: Options{
				PrepareStatusFile: "/nope",
				ProbeTools:        []string{"curl"},
				ProbeAccounts:     []string{"www-data"},
				Getenv:            envFrom(nil),
				LookPath:          lookPathIn(),
				ReadFile:          readFileFrom(nil),
				LookupUser:        lookupUserIn(),
			},
			want: Facts{Tools: []string{}, Accounts: []string{}},
		},
		{
			name: "no status file configured",
			opts: Options{
				Getenv: envFrom(nil), LookPath: lookPathIn(), ReadFile: readFileFrom(nil),
				LookupUser: lookupUserIn(),
			},
			want: Facts{Tools: []string{}, Accounts: []string{}},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := Collect(tc.opts)
			// /.dockerenv may exist on the machine running the test; only the
			// in-cluster case pins Container, the bare case tolerates either.
			if !tc.want.Container {
				got.Container = false
			}
			if !reflect.DeepEqual(got, tc.want) {
				t.Errorf("Collect() = %+v, want %+v", got, tc.want)
			}
		})
	}
}

func TestCollectJSONNeverNullLists(t *testing.T) {
	f := Collect(Options{Getenv: envFrom(nil), LookPath: lookPathIn(), ReadFile: readFileFrom(nil), LookupUser: lookupUserIn()})
	b, err := json.Marshal(f)
	if err != nil {
		t.Fatal(err)
	}
	s := string(b)
	if !strings.Contains(s, `"tools":[]`) || !strings.Contains(s, `"accounts":[]`) {
		t.Errorf("lists must serialise as [], got %s", s)
	}
	for _, k := range []string{"container", "pod_name", "pod_namespace", "node_name", "posture_profile", "prepare_status", "anchor_rename"} {
		if !strings.Contains(s, `"`+k+`"`) {
			t.Errorf("missing JSON key %q in %s", k, s)
		}
	}
}

func TestCollectTruncatesPrepareStatus(t *testing.T) {
	long := strings.Repeat("x", 400)
	f := Collect(Options{
		PrepareStatusFile: "/s", Getenv: envFrom(nil), LookPath: lookPathIn(),
		ReadFile: readFileFrom(map[string]string{"/s": long}), LookupUser: lookupUserIn(),
	})
	if len(f.PrepareStatus) != 253 {
		t.Errorf("PrepareStatus length = %d, want 253", len(f.PrepareStatus))
	}
}

func TestInContainerFromEnv(t *testing.T) {
	if !InContainer(envFrom(map[string]string{"KUBERNETES_SERVICE_HOST": "x"})) {
		t.Error("KUBERNETES_SERVICE_HOST should mean container")
	}
}

// TestDefaultProbeToolsMatchManifest guards the beacon's probe list against
// drifting from core/engine/k8s_manifest.py::_KNOWN_BINS, the list SimCore uses
// to decide which binaries a step needs. A drift would make the preflight
// compare against names the beacon never probed.
func TestDefaultProbeToolsMatchManifest(t *testing.T) {
	var data []byte
	var err error
	for _, c := range []string{
		filepath.Join("..", "..", "core", "engine", "k8s_manifest.py"), // from agent/runtimefacts
		filepath.Join("..", "core", "engine", "k8s_manifest.py"),       // from agent
		filepath.Join("core", "engine", "k8s_manifest.py"),             // from repo root
	} {
		if data, err = os.ReadFile(c); err == nil {
			break
		}
	}
	if err != nil {
		t.Fatalf("could not locate core/engine/k8s_manifest.py: %v", err)
	}
	m := regexp.MustCompile(`(?s)_KNOWN_BINS\s*=\s*\((.*?)\)`).FindSubmatch(data)
	if m == nil {
		t.Fatal("_KNOWN_BINS tuple not found in k8s_manifest.py")
	}
	var want []string
	for _, q := range regexp.MustCompile(`"([^"]+)"`).FindAllSubmatch(m[1], -1) {
		want = append(want, string(q[1]))
	}
	if len(want) == 0 {
		t.Fatal("_KNOWN_BINS parsed empty")
	}
	if !reflect.DeepEqual(want, DefaultProbeTools) {
		t.Errorf("DefaultProbeTools drifted from _KNOWN_BINS\n go: %v\n py: %v", DefaultProbeTools, want)
	}
}
