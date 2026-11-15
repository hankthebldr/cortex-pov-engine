// Package runtimefacts reports what the beacon can observe about the place it
// runs: whether it is in a pod, which pod, which tools and accounts exist.
//
// SimCore stores this as the agent's runtime_facts and uses it to refuse or
// annotate a launch whose steps need a binary the pod does not have. The values
// are observations, never guesses: a tool is listed only if it resolves on PATH
// right now, an account only if the OS knows it.
package runtimefacts

import (
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/hankthebldr/cortexsim/agent/executor"
)

// The server's limits (core/api/agents.py::RuntimeFacts). Over any of them the
// whole request is a 422 and enrollment stops, so Collect clamps everything.
const (
	maxStringLen = 253 // pod/namespace/node names, posture profile, prepare status
	maxItemLen   = 64  // one entry of Tools or Accounts
	maxItems     = 200 // entries in Tools or Accounts
)

// clampString cuts s to n characters without splitting a UTF-8 rune.
func clampString(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

// clampList drops entries over maxItemLen, then keeps at most maxItems.
func clampList(in []string) []string {
	out := make([]string, 0, len(in))
	for _, v := range in {
		if utf8.RuneCountInString(v) <= maxItemLen {
			out = append(out, v)
		}
	}
	if len(out) > maxItems {
		out = out[:maxItems]
	}
	return out
}

// Facts is the runtime_facts payload. Field names and types are what SimCore
// validates; a wrong type is rejected, so Tools and Accounts are always
// non-nil and serialise as [] rather than null.
type Facts struct {
	Container      bool     `json:"container"`
	PodName        string   `json:"pod_name"`
	PodNamespace   string   `json:"pod_namespace"`
	NodeName       string   `json:"node_name"`
	PostureProfile string   `json:"posture_profile"`
	PrepareStatus  string   `json:"prepare_status"`
	Tools          []string `json:"tools"`
	Accounts       []string `json:"accounts"`
	AnchorRename   bool     `json:"anchor_rename"`
}

// Options carries the probe inputs. Nil functions default to the real os, exec
// and user implementations; tests inject fakes.
type Options struct {
	PostureProfile    string
	PrepareStatusFile string
	ProbeTools        []string
	ProbeAccounts     []string
	Getenv            func(string) string
	LookPath          func(string) (string, error)
	EvalSymlinks      func(string) (string, error)
	SameFile          func(a, b string) bool
	ReadFile          func(string) ([]byte, error)
	LookupUser        func(string) error
}

// DefaultProbeTools mirrors core/engine/k8s_manifest.py::_KNOWN_BINS, the list
// SimCore scans step commands against. A Go test fails if the two drift.
var DefaultProbeTools = []string{
	"curl", "wget", "python3", "python", "kubectl", "nsenter", "jq", "openssl",
	"tar", "gzip", "gunzip", "unzip", "git", "nc", "ncat", "socat", "dig",
	"nmap", "crontab", "systemctl", "sudo", "docker", "crictl", "base64",
	"xxd", "ss", "ip", "sha256sum", "make", "gcc", "perl", "ruby", "node",
	"npm", "pip3", "aws", "az", "gcloud", "rclone", "ffuf", "hydra",
}

// InContainer reports whether this process looks containerised: in a Kubernetes
// pod, or a Docker/Podman container marker file exists.
func InContainer(getenv func(string) string) bool {
	if getenv == nil {
		getenv = os.Getenv
	}
	if getenv("KUBERNETES_SERVICE_HOST") != "" {
		return true
	}
	for _, p := range []string{"/.dockerenv", "/run/.containerenv"} {
		if _, err := os.Stat(p); err == nil {
			return true
		}
	}
	return false
}

// Collect gathers the facts. It never fails: anything unobservable is simply
// absent (empty string, empty list, false).
func Collect(opts Options) Facts {
	getenv := opts.Getenv
	if getenv == nil {
		getenv = os.Getenv
	}
	lookPath := opts.LookPath
	if lookPath == nil {
		lookPath = exec.LookPath
	}
	sameFile := opts.SameFile
	if sameFile == nil {
		sameFile = executor.SameFile
	}
	evalSymlinks := opts.EvalSymlinks
	if evalSymlinks == nil {
		evalSymlinks = filepath.EvalSymlinks
	}
	readFile := opts.ReadFile
	if readFile == nil {
		readFile = os.ReadFile
	}
	lookupUser := opts.LookupUser
	if lookupUser == nil {
		lookupUser = func(n string) error { _, err := user.Lookup(n); return err }
	}

	f := Facts{
		Container:      InContainer(getenv),
		PodName:        getenv("POD_NAME"),
		PodNamespace:   getenv("POD_NAMESPACE"),
		NodeName:       getenv("NODE_NAME"),
		PostureProfile: opts.PostureProfile,
		Tools:          []string{},
		Accounts:       []string{},
	}

	if opts.PrepareStatusFile != "" {
		if b, err := readFile(opts.PrepareStatusFile); err == nil {
			line := string(b)
			if i := strings.IndexAny(line, "\r\n"); i >= 0 {
				line = line[:i]
			}
			f.PrepareStatus = line
		}
	}

	for _, t := range opts.ProbeTools {
		if _, err := lookPath(t); err == nil {
			f.Tools = append(f.Tools, t)
		}
	}
	sort.Strings(f.Tools)

	for _, a := range opts.ProbeAccounts {
		if lookupUser(a) == nil {
			f.Accounts = append(f.Accounts, a)
		}
	}
	sort.Strings(f.Accounts)

	f.PodName = clampString(f.PodName, maxStringLen)
	f.PodNamespace = clampString(f.PodNamespace, maxStringLen)
	f.NodeName = clampString(f.NodeName, maxStringLen)
	f.PostureProfile = clampString(f.PostureProfile, maxStringLen)
	f.PrepareStatus = clampString(f.PrepareStatus, maxStringLen)
	f.Tools = clampList(f.Tools)
	f.Accounts = clampList(f.Accounts)

	// The executor's own rule, over this probe's injected seams.
	f.AnchorRename = executor.AnchorRenameSupportedWith(lookPath, evalSymlinks, sameFile)
	return f
}
