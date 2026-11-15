//go:build windows

package identity

import (
	"os/exec"
	"strings"
	"testing"
)

// CONT-14. TOKEN_ELEVATION must agree with the token's integrity level, which
// `whoami /groups` reports independently: High (S-1-16-12288) or System
// (S-1-16-16384) is elevated, Medium (S-1-16-8192) is a filtered token. Runs
// only on Windows (the windows-native-ci job); not observed passing by the
// change that introduced it.
func TestTokenElevated_AgreesWithTheIntegrityLevel(t *testing.T) {
	out, err := exec.Command("whoami", "/groups").Output()
	if err != nil {
		t.Fatalf("whoami /groups: %v", err)
	}
	groups := string(out)
	high := strings.Contains(groups, "S-1-16-12288") || strings.Contains(groups, "S-1-16-16384")
	medium := strings.Contains(groups, "S-1-16-8192")
	if !high && !medium {
		t.Fatalf("whoami /groups reported no recognisable integrity level:\n%s", groups)
	}
	if got := tokenElevated(); got != high {
		t.Fatalf("tokenElevated() = %v, but the integrity level says elevated=%v:\n%s", got, high, groups)
	}
	if ProcessElevated() != tokenElevated() {
		t.Fatalf("ProcessElevated() disagrees with tokenElevated()")
	}
}
