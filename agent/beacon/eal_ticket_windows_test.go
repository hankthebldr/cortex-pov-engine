//go:build windows

package beacon

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// On Windows a step runs as the beacon's own account, so the ticket directory
// and file carry an access list for that account alone, set by the call that
// creates them. icacls reads the list back.
func TestTicketFileModeAndOwner(t *testing.T) {
	dir, err := newTicketDir(false)
	if err != nil {
		t.Fatalf("newTicketDir: %v", err)
	}
	defer os.RemoveAll(dir)
	file := filepath.Join(dir, "ticket")
	if err := writeTicketFile(file, "0123456789abcdef", nil); err != nil {
		t.Fatalf("writeTicketFile: %v", err)
	}
	if raw, err := os.ReadFile(file); err != nil || string(raw) != "0123456789abcdef\n" {
		t.Errorf("the beacon's own account cannot read the ticket back: %q, %v", raw, err)
	}
	// The file is created, never adopted: a path that exists is refused.
	if err := writeTicketFile(file, "another", nil); err == nil {
		t.Error("writeTicketFile wrote over an existing file")
	}

	for _, path := range []string{dir, file} {
		out, err := exec.Command("icacls", path).CombinedOutput()
		if err != nil {
			t.Skipf("icacls is not usable here: %v\n%s", err, out)
		}
		listing := string(out)
		for _, broad := range []string{"Everyone", `BUILTIN\Users`, "Authenticated Users"} {
			if strings.Contains(listing, broad) {
				t.Errorf("%s is open to %s:\n%s", path, broad, listing)
			}
		}
		// One entry, for one account, and none of it inherited.
		if n := strings.Count(listing, ":("); n != 1 || strings.Contains(listing, "(I)") {
			t.Errorf("%s has %d access entries, want exactly one that is its own:\n%s", path, n, listing)
		}
	}
}
