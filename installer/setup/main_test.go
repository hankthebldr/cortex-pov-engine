package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestRun_VersionHelpAndUsageExitCodes(t *testing.T) {
	version = "1.3.0-test"

	var out, errOut bytes.Buffer
	if code := run([]string{"--version"}, &out, &errOut); code != 0 || out.String() != "1.3.0-test\n" {
		t.Errorf("--version: exit %d, stdout %q", code, out.String())
	}

	out.Reset()
	errOut.Reset()
	if code := run([]string{"--help"}, &out, &errOut); code != 0 || !strings.Contains(out.String(), "Usage:") || errOut.Len() != 0 {
		t.Errorf("--help: exit %d, stdout %q, stderr %q", code, out.String(), errOut.String())
	}

	for _, args := range [][]string{{"frobnicate"}, {"install", "--frobnicate"}} {
		out.Reset()
		errOut.Reset()
		code := run(args, &out, &errOut)
		if code != 2 || !strings.Contains(errOut.String(), "Usage:") || out.Len() != 0 {
			t.Errorf("%q: exit %d, stdout %q, stderr %q; want exit 2 with the usage on stderr", args, code, out.String(), errOut.String())
		}
	}
}
