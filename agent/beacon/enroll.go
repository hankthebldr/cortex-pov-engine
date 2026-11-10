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

	CodeEnrollDenied      = "ENROLL_DENIED"
	CodeServerUnreachable = "SERVER_UNREACHABLE"
)

// ErrEnrollTokenEmpty means the token file exists but holds nothing.
var ErrEnrollTokenEmpty = errors.New("ENROLL_TOKEN_EMPTY: enrollment token file is empty")

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
// last 6 characters, as the server does.
func TokenSuffix(token string) string {
	if len(token) <= 6 {
		return token
	}
	return token[len(token)-6:]
}

// ReadTokenFile reads and trims an enrollment token file.
func ReadTokenFile(path string) (string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read enrollment token file: %w", err)
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
	client := &http.Client{Timeout: 30 * time.Second}
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
// again. If the id cannot be persisted the id is still returned alongside the
// error so the caller can run this life; the next restart will enroll afresh.
func LoadOrEnroll(stateDir string, enroll func() (string, error)) (agentID string, reused bool, err error) {
	path := filepath.Join(stateDir, agentIDFile)
	if b, rerr := os.ReadFile(path); rerr == nil {
		if id := strings.TrimSpace(string(b)); id != "" {
			return id, true, nil
		}
	}
	id, err := enroll()
	if err != nil {
		return "", false, err
	}
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return id, false, fmt.Errorf("store agent id: %w", err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, []byte(id+"\n"), 0o600); err != nil {
		return id, false, fmt.Errorf("store agent id: %w", err)
	}
	if err := os.Rename(tmp, path); err != nil {
		os.Remove(tmp)
		return id, false, fmt.Errorf("store agent id: %w", err)
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
