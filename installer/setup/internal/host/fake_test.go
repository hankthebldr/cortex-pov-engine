package host

import (
	"context"
	"errors"
	"io/fs"
	"os/exec"
	"reflect"
	"testing"
)

func TestFake_AnswersFromItsTablesAndRecordsEveryCall(t *testing.T) {
	ctx := context.Background()
	f := &Fake{
		Files: map[string][]byte{"/etc/os-release": []byte("ID=ubuntu\n")},
		Responses: map[string]FakeResponse{
			"systemctl is-active povengine.service": {Stdout: []byte("active\n")},
			"http://127.0.0.1:8888/api/health":      {Status: 200, Body: []byte(`{"status":"degraded"}`)},
		},
		Paths: map[string]string{"systemctl": "/usr/bin/systemctl"},
	}
	var h Host = f

	out, _, err := h.Run(ctx, "systemctl", "is-active", "povengine.service")
	if err != nil || string(out) != "active\n" {
		t.Errorf("Run = %q, %v", out, err)
	}
	if _, _, err := h.Run(ctx, "docker", "ps"); !errors.Is(err, ErrNoFakeResponse) {
		t.Errorf("unconfigured Run error = %v, want ErrNoFakeResponse", err)
	}
	if b, err := h.ReadFile("/etc/os-release"); err != nil || string(b) != "ID=ubuntu\n" {
		t.Errorf("ReadFile = %q, %v", b, err)
	}
	if _, err := h.ReadFile("/etc/missing"); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("ReadFile(missing) error = %v, want fs.ErrNotExist", err)
	}
	if err := h.MkdirAll("/etc/povengine", 0o700); err != nil {
		t.Fatal(err)
	}
	if err := h.WriteFile("/etc/povengine/simcore.env", []byte("A=1\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if fi, err := h.Stat("/etc/povengine/simcore.env"); err != nil || fi.IsDir() || fi.Mode().Perm() != 0o600 || fi.Size() != 4 {
		t.Errorf("Stat(file) = %v, %v", fi, err)
	}
	if fi, err := h.Stat("/etc/povengine"); err != nil || !fi.IsDir() {
		t.Errorf("Stat(dir) = %v, %v", fi, err)
	}
	if p, err := h.LookPath("systemctl"); err != nil || p != "/usr/bin/systemctl" {
		t.Errorf("LookPath = %q, %v", p, err)
	}
	if _, err := h.LookPath("pkexec"); !errors.Is(err, exec.ErrNotFound) {
		t.Errorf("LookPath(absent) error = %v, want exec.ErrNotFound", err)
	}
	if code, body, err := h.HTTPGet(ctx, "http://127.0.0.1:8888/api/health"); err != nil || code != 200 || string(body) != `{"status":"degraded"}` {
		t.Errorf("HTTPGet = %d, %q, %v", code, body, err)
	}
	if err := h.RemoveAll("/etc/povengine"); err != nil {
		t.Fatal(err)
	}
	if _, err := h.Stat("/etc/povengine/simcore.env"); !errors.Is(err, fs.ErrNotExist) {
		t.Errorf("Stat after RemoveAll error = %v, want fs.ErrNotExist", err)
	}

	var got []string
	for _, c := range f.Calls {
		got = append(got, c.Method)
	}
	want := []string{"Run", "Run", "ReadFile", "ReadFile", "MkdirAll", "WriteFile", "Stat", "Stat", "LookPath", "LookPath", "HTTPGet", "RemoveAll", "Stat"}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("calls = %v\nwant    %v", got, want)
	}
	if w := f.Calls[5]; w.Mode != 0o600 || !reflect.DeepEqual(w.Args, []string{"/etc/povengine/simcore.env"}) || string(w.Data) != "A=1\n" {
		t.Errorf("WriteFile call = %+v, want the path, mode 0600 and the bytes written", w)
	}
	if r := f.Calls[0]; !reflect.DeepEqual(r.Args, []string{"systemctl", "is-active", "povengine.service"}) {
		t.Errorf("Run call args = %q", r.Args)
	}
}
