package runtimefacts

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"runtime"
	"strings"
	"testing"
	"unicode/utf8"
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

// wantAnchor is the AnchorRename this platform's executor rule gives for a case
// whose POSIX answer is posix. On Windows the Args[0] rewrite needs no shell, so
// the rule is always true whatever the seams report (executor/platform_windows.go).
func wantAnchor(posix bool) bool {
	if runtime.GOOS == "windows" {
		return true
	}
	return posix
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
			want: Facts{Tools: []string{}, Accounts: []string{}, AnchorRename: wantAnchor(false)},
		},
		{
			name: "no status file configured",
			opts: Options{
				Getenv: envFrom(nil), LookPath: lookPathIn(), ReadFile: readFileFrom(nil),
				LookupUser: lookupUserIn(),
			},
			want: Facts{Tools: []string{}, Accounts: []string{}, AnchorRename: wantAnchor(false)},
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

func TestCollectClampsStringsToServerLimit(t *testing.T) {
	long := strings.Repeat("a", 400)
	f := Collect(Options{
		PostureProfile:    long,
		PrepareStatusFile: "/s",
		Getenv:            envFrom(map[string]string{"POD_NAME": long, "POD_NAMESPACE": long, "NODE_NAME": long}),
		LookPath:          lookPathIn(),
		ReadFile:          readFileFrom(map[string]string{"/s": long}),
		LookupUser:        lookupUserIn(),
	})
	for name, v := range map[string]string{
		"PodName": f.PodName, "PodNamespace": f.PodNamespace, "NodeName": f.NodeName,
		"PostureProfile": f.PostureProfile, "PrepareStatus": f.PrepareStatus,
	} {
		if n := len([]rune(v)); n != 253 {
			t.Errorf("%s has %d chars, want 253", name, n)
		}
	}
}

func TestCollectClampDoesNotSplitRunes(t *testing.T) {
	// 252 ASCII + a 3-byte rune + more: cutting at byte 253 would split the rune.
	v := strings.Repeat("a", 252) + "€€€"
	f := Collect(Options{
		Getenv:     envFrom(map[string]string{"POD_NAME": v}),
		LookPath:   lookPathIn(),
		LookupUser: lookupUserIn(),
	})
	if !utf8.ValidString(f.PodName) {
		t.Fatalf("clamped value is not valid UTF-8: %q", f.PodName[240:])
	}
	if n := len([]rune(f.PodName)); n != 253 {
		t.Errorf("rune count = %d, want 253", n)
	}
	if _, err := json.Marshal(f); err != nil {
		t.Fatal(err)
	}
}

func TestCollectCapsListsAndDropsOverlongItems(t *testing.T) {
	var names []string
	for i := 0; i < 300; i++ {
		names = append(names, fmt.Sprintf("t%03d", i))
	}
	names = append(names, strings.Repeat("x", 65), strings.Repeat("y", 64))
	all := func(string) (string, error) { return "/bin/x", nil }
	f := Collect(Options{
		ProbeTools:    names,
		ProbeAccounts: names,
		Getenv:        envFrom(nil),
		LookPath:      all,
		LookupUser:    func(string) error { return nil },
	})
	for name, got := range map[string][]string{"Tools": f.Tools, "Accounts": f.Accounts} {
		if len(got) != 200 {
			t.Errorf("%s has %d items, want 200", name, len(got))
		}
		for _, it := range got {
			if len(it) > 64 {
				t.Errorf("%s kept an over-long item (%d chars)", name, len(it))
			}
		}
	}
	// The 64-char name is legal and sorts after t299, so it is cut by the cap,
	// not by the length rule; the 65-char one must be gone either way.
	g := Collect(Options{
		ProbeTools: []string{strings.Repeat("x", 65), strings.Repeat("y", 64)},
		Getenv:     envFrom(nil), LookPath: all, LookupUser: lookupUserIn(),
	})
	if len(g.Tools) != 1 || len(g.Tools[0]) != 64 {
		t.Errorf("Tools = %v, want only the 64-char item", g.Tools)
	}
}

// AnchorRename is the executor's anchor rule over Collect's injected seams: a
// single-purpose sh is renamed; a busybox/toybox sh needs bash. On Windows the
// rule ignores the seams and every case is true (wantAnchor).
func TestCollectAnchorRenameFollowsTheShRule(t *testing.T) {
	links := func(target string) func(string) (string, error) {
		return func(p string) (string, error) {
			if p == "/usr/bin/sh" {
				return target, nil
			}
			return p, nil
		}
	}
	cases := []struct {
		name    string
		present []string
		target  string
		want    bool
	}{
		{"dash sh without bash", []string{"sh"}, "/usr/bin/dash", true},
		{"busybox sh with bash", []string{"sh", "bash"}, "/bin/busybox", true},
		{"busybox sh without bash", []string{"sh"}, "/bin/busybox", false},
		{"toybox sh without bash", []string{"sh"}, "/system/bin/toybox", false},
	}
	for _, c := range cases {
		f := Collect(Options{
			Getenv: envFrom(nil), LookPath: lookPathIn(c.present...),
			EvalSymlinks: links(c.target), ReadFile: readFileFrom(nil),
			LookupUser: lookupUserIn(),
		})
		if want := wantAnchor(c.want); f.AnchorRename != want {
			t.Errorf("%s: AnchorRename = %v, want %v", c.name, f.AnchorRename, want)
		}
	}
}

// A hard-linked busybox sh has no symlink for EvalSymlinks to follow; the
// executor's rule compares it with `busybox` on PATH through the SameFile seam.
// On Windows every case is true (wantAnchor).
func TestCollectAnchorRenameDetectsHardLinkedBusybox(t *testing.T) {
	same := func(a, b string) bool { return a == "/usr/bin/sh" && b == "/usr/bin/busybox" }
	cases := []struct {
		name    string
		present []string
		want    bool
	}{
		{"hard-linked busybox sh without bash", []string{"sh", "busybox"}, false},
		{"hard-linked busybox sh with bash", []string{"sh", "busybox", "bash"}, true},
	}
	for _, c := range cases {
		f := Collect(Options{
			Getenv: envFrom(nil), LookPath: lookPathIn(c.present...),
			EvalSymlinks: func(p string) (string, error) { return p, nil },
			SameFile:     same, ReadFile: readFileFrom(nil), LookupUser: lookupUserIn(),
		})
		if want := wantAnchor(c.want); f.AnchorRename != want {
			t.Errorf("%s: AnchorRename = %v, want %v", c.name, f.AnchorRename, want)
		}
	}
}
