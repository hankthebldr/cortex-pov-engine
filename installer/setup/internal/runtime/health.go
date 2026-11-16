package runtime

import (
	"encoding/json"
	"errors"
	"fmt"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
)

// HealthVerdict is the installer's reading of one /api/health body.
type HealthVerdict struct {
	// Code is OK, MASTER_KEY_MISMATCH, or SIMCORE_UNHEALTHY when the body
	// could not be read.
	Code engine.Code
	// Faults are the degraded components other than api_auth, in SimCore's
	// order. A healthy install still lists them: it completes, and says what
	// is wrong instead of reading "ok".
	Faults []string
}

// apiAuth is degraded on every current deployment by design (no per-request
// authentication yet), so it never counts as a fault.
const apiAuth = "api_auth"

// JudgeHealth reads a GET /api/health body. A body it does not recognise (not
// JSON, no components object, no degraded_components list) is an error with
// Code SIMCORE_UNHEALTHY, never a healthy reading: an unknown shape that read
// as "no faults" would let a broken install report success.
func JudgeHealth(body []byte) (HealthVerdict, error) {
	unhealthy := HealthVerdict{Code: engine.CodeSimCoreUnhealthy}
	var doc struct {
		Components map[string]json.RawMessage `json:"components"`
		Degraded   *[]string                  `json:"degraded_components"`
	}
	if err := json.Unmarshal(body, &doc); err != nil {
		return unhealthy, fmt.Errorf("/api/health body is not the expected JSON: %w", err)
	}
	if doc.Components == nil {
		return unhealthy, errors.New("/api/health body has no components object")
	}
	if doc.Degraded == nil {
		return unhealthy, errors.New("/api/health body has no degraded_components list")
	}

	v := HealthVerdict{Code: engine.CodeOK}
	for _, name := range *doc.Degraded {
		if name != apiAuth {
			v.Faults = append(v.Faults, name)
		}
	}
	if raw, ok := doc.Components["master_key"]; ok {
		var mk struct {
			Code string `json:"code"`
		}
		if err := json.Unmarshal(raw, &mk); err != nil {
			return unhealthy, fmt.Errorf("/api/health master_key component is not an object: %w", err)
		}
		if mk.Code == string(engine.CodeMasterKeyMismatch) {
			v.Code = engine.CodeMasterKeyMismatch
		}
	}
	return v, nil
}
