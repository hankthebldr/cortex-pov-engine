package beacon

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/hankthebldr/cortexsim/agent/runtimefacts"
)

// Native enrollment: a pod has no curl, so the beacon redeems its enrollment
// token itself and remembers the agent id it was given across container
// restarts. The shell installer's POST /api/agents/enroll is the same contract.

const (
	// agentIDFile is the file under the state dir that holds the enrolled id.
	agentIDFile = "agent-id"
	// maxDesiredName is the server's limit on desired_name; longer is a 422.
	maxDesiredName = 60
	// enrollResponseLimit bounds how much of a response body is read.
	enrollResponseLimit = 1 << 20

	CodeEnrollDenied       = "ENROLL_DENIED"
	CodeServerUnreachable  = "SERVER_UNREACHABLE"
	CodeStateDirUnwritable = "STATE_DIR_UNWRITABLE"
)

// ErrEnrollTokenEmpty means the token file exists but holds nothing.
var ErrEnrollTokenEmpty = errors.New("ENROLL_TOKEN_EMPTY: enrollment token file is empty")

// ErrEnrollTokenUnreadable means the token file could not be read at all.
var ErrEnrollTokenUnreadable = errors.New("ENROLL_TOKEN_UNREADABLE")

// StateDirError means the agent-id store cannot be read or written. It is
// raised BEFORE the token is redeemed: enrolling without being able to remember
// the id would spend a use on every container restart until the token is gone.
type StateDirError struct {
	Op  string
	Err error
}

func (e *StateDirError) Error() string {
	return fmt.Sprintf("%s: %s: %v", CodeStateDirUnwritable, e.Op, e.Err)
}
func (e *StateDirError) Unwrap() error { return e.Err }

// EnrollError is a failed enrollment. Status is the HTTP status (0 when no
// response was received); Code is the server's structured error code, or
// SERVER_UNREACHABLE for a transport failure. It never carries the token.
type EnrollError struct {
	Status int
	Code   string
	Detail string
}

func (e *EnrollError) Error() string {
	msg := fmt.Sprintf("enroll failed: %s", e.Code)
	if e.Status != 0 {
		msg += fmt.Sprintf(" (HTTP %d)", e.Status)
	}
	if e.Detail != "" {
		msg += ": " + e.Detail
	}
	return msg
}

// NormalizeServerURL trims whitespace and trailing slashes. The server URL
// comes from a Kubernetes Secret and routinely carries a newline or a slash.
func NormalizeServerURL(s string) string {
	return strings.TrimRight(strings.TrimSpace(s), "/")
}

// TokenSuffix is the only part of an enrollment token that may be logged: its
// last 6 characters, as the server does. A token of 6 or fewer characters would
// be disclosed whole, so it yields "…" instead.
func TokenSuffix(token string) string {
	if len(token) <= 6 {
		return "…"
	}
	return token[len(token)-6:]
}

// ReadTokenFile reads and trims an enrollment token file.
func ReadTokenFile(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("%w: %v", ErrEnrollTokenUnreadable, err)
	}
	tok := strings.TrimSpace(string(b))
	if tok == "" {
		return "", ErrEnrollTokenEmpty
	}
	return tok, nil
}

func clampRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

// Enroll redeems token at POST /api/agents/enroll and returns the agent id the
// server assigned. desiredName is clamped to the server's 60-character limit.
func Enroll(serverURL, token, hostname, goos, desiredName string,
	capabilities, interpreters []string, facts *runtimefacts.Facts) (string, error) {

	body := map[string]interface{}{
		"token":        token,
		"hostname":     hostname,
		"os":           goos,
		"capabilities": capabilities,
		"interpreters": interpreters,
		"desired_name": clampRunes(desiredName, maxDesiredName),
	}
	if facts != nil {
		body["runtime_facts"] = facts
	}
	raw, err := json.Marshal(body)
	if err != nil {
		return "", fmt.Errorf("enroll: encode body: %w", err)
	}

	url := NormalizeServerURL(serverURL) + "/api/agents/enroll"
	client := &http.Client{
		Timeout: 30 * time.Second,
		// A 307/308 would re-send the token to whatever host the response names.
		// A redirect is surfaced as a non-2xx failure instead.
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	resp, err := client.Post(url, "application/json", bytes.NewReader(raw))
	if err != nil {
		// net/http errors name the URL, never the request body.
		return "", &EnrollError{Code: CodeServerUnreachable, Detail: err.Error()}
	}
	defer resp.Body.Close()
	payload, _ := io.ReadAll(io.LimitReader(resp.Body, enrollResponseLimit))

	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		code, detail := parseErrorBody(payload)
		if code == "" && resp.StatusCode == http.StatusForbidden {
			code = CodeEnrollDenied
		}
		return "", &EnrollError{Status: resp.StatusCode, Code: code, Detail: detail}
	}

	var ok struct {
		AgentID string `json:"agent_id"`
	}
	if err := json.Unmarshal(payload, &ok); err != nil {
		return "", fmt.Errorf("enroll: unreadable response: %w", err)
	}
	if strings.TrimSpace(ok.AgentID) == "" {
		return "", errors.New("enroll: server returned no agent_id")
	}
	return ok.AgentID, nil
}

// parseErrorBody pulls {code, detail-ish} out of an error body. FastAPI wraps
// structured errors under a top-level "detail" key; the code is accepted at
// either level. A non-JSON body yields empty strings.
func parseErrorBody(b []byte) (code, detail string) {
	var top struct {
		Code   string          `json:"code"`
		Error  string          `json:"error"`
		Detail json.RawMessage `json:"detail"`
	}
	if json.Unmarshal(b, &top) != nil {
		return "", ""
	}
	code, detail = top.Code, top.Error
	var nested struct {
		Code   string `json:"code"`
		Error  string `json:"error"`
		Detail string `json:"detail"`
	}
	if len(top.Detail) > 0 && json.Unmarshal(top.Detail, &nested) == nil {
		if code == "" {
			code = nested.Code
		}
		if detail == "" {
			detail = nested.Error
		}
	}
	return code, detail
}

// LoadOrEnroll returns the agent id stored under stateDir, enrolling (and
// storing the result) only when there is none. A stored id means the pod was
// restarted: the token is typically spent by then, so it must not be redeemed
// again. Before enrolling it proves the store is readable and writable; if not
// it returns a *StateDirError WITHOUT calling enroll, so no token use is spent
// on an id that could not be remembered.
func LoadOrEnroll(stateDir string, enroll func() (string, error)) (agentID string, reused bool, err error) {
	path := filepath.Join(stateDir, agentIDFile)
	b, rerr := os.ReadFile(path)
	switch {
	case rerr == nil:
		if id := strings.TrimSpace(string(b)); id != "" {
			return id, true, nil
		}
	case !errors.Is(rerr, os.ErrNotExist):
		return "", false, &StateDirError{Op: "read agent id", Err: rerr}
	}

	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return "", false, &StateDirError{Op: "create state dir", Err: err}
	}
	probe, err := os.CreateTemp(stateDir, ".write-probe-*")
	if err != nil {
		return "", false, &StateDirError{Op: "write probe", Err: err}
	}
	probe.Close()
	os.Remove(probe.Name())

	id, err := enroll()
	if err != nil {
		return "", false, err
	}
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return id, false, &StateDirError{Op: "store agent id", Err: err}
	}
	// OpenFile's mode is masked by umask and ignored for a pre-existing file.
	if err := f.Chmod(0o600); err == nil {
		_, err = f.WriteString(id + "\n")
		if cerr := f.Close(); err == nil {
			err = cerr
		}
	} else {
		f.Close()
	}
	if err == nil {
		err = os.Rename(tmp, path)
	}
	if err != nil {
		os.Remove(tmp)
		return id, false, &StateDirError{Op: "store agent id", Err: err}
	}
	return id, false, nil
}

// EnrollWithRetry calls fn up to attempts times, sleeping delay between tries,
// but only while it fails with SERVER_UNREACHABLE. Any other outcome (success,
// a denied token) returns at once: retrying a spent token helps nobody.
func EnrollWithRetry(attempts int, delay time.Duration, sleep func(time.Duration),
	fn func() (string, error)) (string, error) {
	var id string
	var err error
	for i := 0; i < attempts; i++ {
		id, err = fn()
		var ee *EnrollError
		if err == nil || !errors.As(err, &ee) || ee.Code != CodeServerUnreachable {
			return id, err
		}
		if i < attempts-1 {
			sleep(delay)
		}
	}
	return id, err
}
