package main

import (
	"os"
	"os/exec"
	"strings"
	"testing"
)

// The helper re-executes this test binary as the agent so the real main() and
// its os.Exit paths are what the assertions observe.
func TestMainFlagValidationExits(t *testing.T) {
	if args := os.Getenv("CORTEXSIM_TEST_MAIN_ARGS"); args != "" {
		os.Args = append([]string{"cortexsim-agent"}, strings.Fields(args)...)
		main()
		os.Exit(0)
	}
	cases := map[string][2]string{
		"both id and token file": {"--id x --enroll-token-file /nonexistent", "mutually exclusive"},
		"neither":                {"--interval 10", "--id or --enroll-token-file is required"},
	}
	for name, c := range cases {
		args, wantMsg := c[0], c[1]
		t.Run(name, func(t *testing.T) {
			cmd := exec.Command(os.Args[0], "-test.run=TestMainFlagValidationExits")
			cmd.Env = append(os.Environ(), "CORTEXSIM_TEST_MAIN_ARGS="+args)
			out, err := cmd.CombinedOutput()
			ee, ok := err.(*exec.ExitError)
			if !ok || ee.ExitCode() == 0 {
				t.Fatalf("want non-zero exit, got err=%v\n%s", err, out)
			}
			if !strings.Contains(string(out), wantMsg) {
				t.Errorf("output %q does not mention %q", out, wantMsg)
			}
		})
	}
}

func TestValidateIdentityFlags(t *testing.T) {
	if err := validateIdentityFlags("x", ""); err != nil {
		t.Error(err)
	}
	if err := validateIdentityFlags("", "/t"); err != nil {
		t.Error(err)
	}
	if validateIdentityFlags("x", "/t") == nil {
		t.Error("both must be an error")
	}
	if validateIdentityFlags("", "") == nil {
		t.Error("neither must be an error")
	}
}
