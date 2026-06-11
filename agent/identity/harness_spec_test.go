package identity

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// identityHarnessSpec mirrors the fields of spec/identity_harness.json that the
// Go beacon cares about. The JSON is the SINGLE source of truth shared with the
// push-mode bash generator (core/engine/push_generator.py via
// core/engine/identity_spec.py). This test guards against the Go allowlist
// drifting from the canonical spec (GAP-PUSH-001).
type identityHarnessSpec struct {
	Version          int      `json:"version"`
	DirectIdentities []string `json:"direct_identities"`
	ServiceAccounts  []string `json:"service_accounts"`
}

// loadSpec walks up from the test's working directory (agent/identity) to the
// repo root and reads spec/identity_harness.json. It tolerates being run from
// a few directory depths so `go test ./...` from the module root works too.
func loadSpec(t *testing.T) identityHarnessSpec {
	t.Helper()
	candidates := []string{
		filepath.Join("..", "..", "spec", "identity_harness.json"), // from agent/identity
		filepath.Join("..", "spec", "identity_harness.json"),        // from agent
		filepath.Join("spec", "identity_harness.json"),              // from repo root
	}
	var data []byte
	var err error
	for _, c := range candidates {
		data, err = os.ReadFile(c)
		if err == nil {
			break
		}
	}
	if err != nil {
		t.Fatalf("could not locate spec/identity_harness.json from any candidate path: %v", err)
	}
	var spec identityHarnessSpec
	if err := json.Unmarshal(data, &spec); err != nil {
		t.Fatalf("identity_harness.json is not valid JSON: %v", err)
	}
	return spec
}

// TestServiceAccountsMatchCanonicalSpec asserts the Go serviceAccounts map is
// exactly the canonical service-account set. If they drift, push and pull mode
// would resolve the same scenario identity differently.
func TestServiceAccountsMatchCanonicalSpec(t *testing.T) {
	spec := loadSpec(t)

	if len(spec.ServiceAccounts) == 0 {
		t.Fatal("canonical spec has no service_accounts")
	}

	// Every canonical service account must resolve to runuser (known, not unknown).
	for _, acct := range spec.ServiceAccounts {
		if !serviceAccounts[acct] {
			t.Errorf("canonical service account %q is missing from Go serviceAccounts map", acct)
		}
		mode, user, unknown := ResolveIdentity(acct)
		if mode != "runuser" || user != acct || unknown {
			t.Errorf("ResolveIdentity(%q) = (%q,%q,%v); want (runuser,%q,false)",
				acct, mode, user, unknown, acct)
		}
	}

	// And the Go map must not carry extras the spec doesn't know about.
	canonical := make(map[string]bool, len(spec.ServiceAccounts))
	for _, a := range spec.ServiceAccounts {
		canonical[a] = true
	}
	for acct := range serviceAccounts {
		if !canonical[acct] {
			t.Errorf("Go serviceAccounts has %q which is absent from the canonical spec", acct)
		}
	}
}

// TestDirectIdentitiesMatchCanonicalSpec asserts every canonical direct
// identity resolves to direct mode (no impersonation wrapper).
func TestDirectIdentitiesMatchCanonicalSpec(t *testing.T) {
	spec := loadSpec(t)
	for _, ident := range spec.DirectIdentities {
		mode, user, unknown := ResolveIdentity(ident)
		if mode != "direct" || user != "" || unknown {
			t.Errorf("ResolveIdentity(%q) = (%q,%q,%v); want (direct,\"\",false)",
				ident, mode, user, unknown)
		}
	}
}
