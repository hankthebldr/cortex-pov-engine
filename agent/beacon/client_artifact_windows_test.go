//go:build windows

package beacon

// PowerShell-dialect twins of client_artifact_unix_test.go (CI-04): the staged
// tool is a .ps1 the step invokes by path, through both execution paths. The
// permission-trap test has no Windows twin by design: NTFS has no exec bit and
// the beacon cannot impersonate (artifact_windows.go). Run only by the
// windows-native CI job; not observed passing on Windows.

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestExecuteTask_WindowsStagesThenRunsTheStagedTool(t *testing.T) {
	for _, chained := range []bool{false, true} {
		tool := []byte("Write-Output 'STAGED_TOOL_RAN'\r\n")
		root := t.TempDir()
		dest := filepath.Join(root, "run-1", "sysinfo.ps1")
		task := &Task{
			TaskID: "t-1", RunID: "run-1", ScenarioID: "SIM-EDR-999",
			Steps: []Step{{ID: "step-01", MitreTechnique: "T1082", Identity: "root",
				Command: "& " + psQuote(dest)}},
			Artifacts: []Artifact{{
				Name: "winpeas.ps1", SHA256: digestOf(tool), SizeBytes: int64(len(tool)),
				Path: "/api/shelf/payload/winpeas.ps1", Dest: dest, Steps: []string{"step-01"},
			}},
		}
		if chained {
			task.CgoAnchor = &CgoAnchor{ImageName: "w3wp.exe"}
		}
		rs := newArtifactRecordingServer(t, task, map[string][]byte{"winpeas.ps1": tool})
		c := artifactClientFor(t, rs, root)
		c.resolveIdentity = directResolver
		c.executeTask(context.Background(), task)

		joined := strings.Join(bodiesOf(rs.find("POST", "/output")), "\n")
		if !strings.Contains(joined, "STAGED_TOOL_RAN") {
			t.Fatalf("chained=%v: the step did not run the staged tool:\n%s", chained, joined)
		}
		if code, summary := rs.completeBody(t); code != 0 {
			t.Fatalf("chained=%v: exit_code = %d, summary = %s", chained, code, summary)
		}
		if _, err := os.Stat(dest); !os.IsNotExist(err) {
			t.Fatalf("chained=%v: staged tooling survived the run at %s", chained, dest)
		}
	}
}

func psQuote(s string) string { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
