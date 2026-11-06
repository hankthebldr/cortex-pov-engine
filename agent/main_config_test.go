package main

// Flag parsing moved out of main into parseConfig so a bad service command
// line can be reported through the SCM (a STOPPED status with a
// service-specific exit code, plus a line in agent.log) instead of an exit
// that nothing on a console-less service ever displays. These tests pin that
// the move kept the console contract byte-for-byte: same defaults, same
// bounds, same exit codes.

import (
	"errors"
	"flag"
	"strings"
	"testing"
	"time"
)

func noEnv(string) string { return "" }

func TestParseConfigDefaults(t *testing.T) {
	cfg, err := parseConfig([]string{"--id", "host-01"}, noEnv)
	if err != nil {
		t.Fatalf("parseConfig: %v", err)
	}
	want := beaconConfig{server: defaultServer, id: "host-01", interval: 10 * time.Second}
	if cfg != want {
		t.Errorf("defaults = %+v, want %+v", cfg, want)
	}
}

func TestParseConfigCarriesEveryFlag(t *testing.T) {
	cfg, err := parseConfig([]string{
		"--server", "http://simcore:8888", "--id", "h", "--interval", "3",
		"--long-poll", "25", "--stream-output", "--step-timeout", "600",
		"--artifact-token", "tok-flag", "--log-file", `C:\logs\b.log`,
	}, noEnv)
	if err != nil {
		t.Fatalf("parseConfig: %v", err)
	}
	want := beaconConfig{
		server: "http://simcore:8888", id: "h", interval: 3 * time.Second,
		longPoll: 25, streamOutput: true, stepTimeout: 600 * time.Second,
		artifactToken: "tok-flag", logFile: `C:\logs\b.log`,
	}
	if cfg != want {
		t.Errorf("cfg = %+v, want %+v", cfg, want)
	}
}

func TestParseConfigArtifactTokenEnvFallback(t *testing.T) {
	env := func(k string) string {
		if k == "CORTEXSIM_ARTIFACT_TOKEN" {
			return "tok-env"
		}
		return ""
	}
	cfg, err := parseConfig([]string{"--id", "h"}, env)
	if err != nil || cfg.artifactToken != "tok-env" {
		t.Errorf("env token not used: cfg=%+v err=%v", cfg, err)
	}
	cfg, err = parseConfig([]string{"--id", "h", "--artifact-token", "tok-flag"}, env)
	if err != nil || cfg.artifactToken != "tok-flag" {
		t.Errorf("--artifact-token must beat the env: cfg=%+v err=%v", cfg, err)
	}
}

// TestParseConfigRejectsWithConsoleExitCodes: validation failures exit 1 and
// flag-syntax failures exit 2, exactly as flag.ExitOnError and the old inline
// checks did.
func TestParseConfigRejectsWithConsoleExitCodes(t *testing.T) {
	cases := []struct {
		name     string
		args     []string
		exit     int
		contains string
	}{
		{"missing id", nil, 1, "--id is required"},
		{"interval zero", []string{"--id", "h", "--interval", "0"}, 1, "--interval must be at least 1 second"},
		{"long-poll above 60", []string{"--id", "h", "--long-poll", "61"}, 1, "--long-poll must be between 0 and 60"},
		{"long-poll negative", []string{"--id", "h", "--long-poll", "-1"}, 1, "--long-poll must be between 0 and 60"},
		{"step-timeout negative", []string{"--id", "h", "--step-timeout", "-1"}, 1, "--step-timeout must be >= 0"},
		{"unknown flag", []string{"--id", "h", "--no-such-flag"}, 2, "no-such-flag"},
		{"non-numeric interval", []string{"--id", "h", "--interval", "ten"}, 2, "interval"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := parseConfig(tc.args, noEnv)
			if err == nil {
				t.Fatalf("parseConfig(%v) accepted an invalid command line", tc.args)
			}
			if got := configExitCode(err); got != tc.exit {
				t.Errorf("exit code = %d, want %d (err: %v)", got, tc.exit, err)
			}
			if !strings.Contains(err.Error(), tc.contains) {
				t.Errorf("error %q does not mention %q", err, tc.contains)
			}
		})
	}
}

func TestParseConfigHelpExitsZero(t *testing.T) {
	_, err := parseConfig([]string{"-h"}, noEnv)
	if !errors.Is(err, flag.ErrHelp) {
		t.Fatalf("-h returned %v, want flag.ErrHelp", err)
	}
	if got := configExitCode(err); got != 0 {
		t.Errorf("-h exit code = %d, want 0", got)
	}
}

// TestUsageListsLogFileFlag: the usage text is the only documentation a DC sees
// on the endpoint itself.
func TestUsageListsLogFileFlag(t *testing.T) {
	var b strings.Builder
	printUsage(&b)
	for _, want := range []string{"-log-file", "CORTEXSIM_AGENT_LOG_FILE", `%ProgramData%\CortexSim\agent.log`, "-id", "-server"} {
		if !strings.Contains(b.String(), want) {
			t.Errorf("usage does not mention %q:\n%s", want, b.String())
		}
	}
}
