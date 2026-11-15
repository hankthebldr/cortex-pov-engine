package ealrelay

import (
	"strings"
	"testing"
)

var testInv = Invocation{Exe: "/usr/local/bin/cortexsim-agent", Relay: "127.0.0.1:40123",
	TicketFile: "/tmp/cortexsim-eal-1/0123abcd"}

const posixCall = "'/usr/local/bin/cortexsim-agent' eal --relay 127.0.0.1:40123 --ticket-file '/tmp/cortexsim-eal-1/0123abcd'"

// The step SIM-NDR-001 step-03 runs: a quoted heredoc that writes the campaign,
// then the call. Only the call may change.
const ndrStep = `C=$(mktemp -d /tmp/cortexsim.XXXXXX) || exit 1; trap 'rm -rf "$C"' EXIT
cat > "$C/c2-beacon-live.yml" <<'YML'
campaign_id: CMP-NDR-001
steps:
  - step_id: step-01
    plugin: c2_http_beacon
    params:
      target_url: http://c2.example.test/uid/index.html
YML
python3 -m scripts.eal_simulator.cli run "$C/c2-beacon-live.yml" --live
`

func TestRewriteReplacesOnlyTheCall(t *testing.T) {
	got, n := Rewrite(ndrStep, "linux", "", testInv)
	if n != 1 {
		t.Fatalf("replaced %d calls, want 1", n)
	}
	want := strings.Replace(ndrStep, "python3 -m scripts.eal_simulator.cli", posixCall, 1)
	if got != want {
		t.Errorf("rewrite changed more than the call\n got: %q\nwant: %q", got, want)
	}
}

func TestRewriteShapesInTheCorpus(t *testing.T) {
	cases := []struct{ name, goos, shell, in, want string }{
		{"a prefix command keeps its place", "linux", "",
			`timeout --foreground 100 python3 -m scripts.eal_simulator.cli run "$ROOT/step-01.yml" --live`,
			`timeout --foreground 100 ` + posixCall + ` run "$ROOT/step-01.yml" --live`},
		{"a pipe into jq is untouched", "linux", "",
			`python3 -m scripts.eal_simulator.cli list-plugins | jq -e '.total >= 4'`,
			posixCall + ` list-plugins | jq -e '.total >= 4'`},
		{"a line continuation is untouched", "darwin", "",
			"python3 -m scripts.eal_simulator.cli describe email_emitter \\\n  | jq -e '.eal_targets'",
			posixCall + " describe email_emitter \\\n  | jq -e '.eal_targets'"},
		{"python without the 3", "linux", "",
			`python -m scripts.eal_simulator.cli run /tmp/x.yml`,
			posixCall + ` run /tmp/x.yml`},
		{"PowerShell needs the call operator", "windows", "",
			`python -m scripts.eal_simulator.cli run $sandbox\itdr-002.yml --live`,
			`& 'C:\Program Files\CortexSim\cortexsim-agent.exe' eal --relay 127.0.0.1:40123 --ticket-file 'C:\Windows\Temp\cortexsim-eal-1\0123abcd' run $sandbox\itdr-002.yml --live`},
		{"cmd quotes with double quotes", "windows", "cmd",
			`python.exe -m scripts.eal_simulator.cli list-plugins`,
			`"C:\Program Files\CortexSim\cortexsim-agent.exe" eal --relay 127.0.0.1:40123 --ticket-file "C:\Windows\Temp\cortexsim-eal-1\0123abcd" list-plugins`},
	}
	for _, tc := range cases {
		inv := testInv
		if tc.goos == "windows" {
			inv.Exe = `C:\Program Files\CortexSim\cortexsim-agent.exe`
			inv.TicketFile = `C:\Windows\Temp\cortexsim-eal-1\0123abcd`
		}
		if got, n := Rewrite(tc.in, tc.goos, tc.shell, inv); got != tc.want || n != 1 {
			t.Errorf("%s:\n got: %s (%d)\nwant: %s", tc.name, got, n, tc.want)
		}
	}
}

func TestRewriteLeavesOtherPythonAlone(t *testing.T) {
	for _, in := range []string{
		`python3 -c 'print(1)'`,
		`python3 -m http.server 8000`,
		`# python3 running scripts.eal_simulator.cli (a comment about it)`,
		`echo no call here`,
	} {
		if got, n := Rewrite(in, "linux", "", testInv); got != in || n != 0 {
			t.Errorf("%q was rewritten to %q", in, got)
		}
	}
}

func TestRewriteQuotesAnAwkwardBinaryPath(t *testing.T) {
	inv := Invocation{Exe: "/opt/it's here/agent", Relay: "127.0.0.1:1", TicketFile: "/tmp/d/f"}
	got, _ := Rewrite("python3 -m scripts.eal_simulator.cli list-plugins", "linux", "", inv)
	want := `'/opt/it'\''s here/agent' eal --relay 127.0.0.1:1 --ticket-file '/tmp/d/f' list-plugins`
	if got != want {
		t.Errorf("got %s\nwant %s", got, want)
	}
}
