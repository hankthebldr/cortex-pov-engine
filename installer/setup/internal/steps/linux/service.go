package linux

import (
	"context"
	"fmt"
	"net"
	"strconv"
	"strings"
	"time"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/config"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/runtime"
)

const (
	healthWait  = 120 * time.Second // for the container's health check to pass
	healthEvery = 2 * time.Second
)

// portOf is the chosen port, 8888 when none was given.
func portOf(st *engine.State) int {
	if st.Choices.Port == 0 {
		return defaultPort
	}
	return st.Choices.Port
}

// runInput is the SimCore container the current choices describe.
func runInput(p config.Paths, image string, st *engine.State) runtime.RunInput {
	return runtime.RunInput{
		Image:      image,
		Name:       runtime.ContainerName,
		EnvFile:    p.EnvFile,
		DataDir:    p.DataDir,
		Address:    st.Choices.Address,
		Port:       portOf(st),
		ListenOnly: st.Choices.ListenOnly,
	}
}

// ---------------------------------------------------------------- Service

// Service writes povengine.service and (re)starts it, which recreates the
// SimCore container from the current image, env file and choices.
type Service struct {
	h     host.Host
	p     config.Paths
	image string
}

func NewService(h host.Host, p config.Paths, image string) *Service {
	return &Service{h: h, p: p, image: image}
}

func (*Service) ID() string    { return "service" }
func (*Service) Title() string { return "SimCore service" }

func (s *Service) Check(ctx context.Context, st *engine.State) engine.Result {
	unit := ServiceUnit(runInput(s.p, s.image, st))
	b, err := s.h.ReadFile(ServiceUnitPath)
	if err != nil {
		return ok("Will install " + serviceUnitName)
	}
	out, _ := run(ctx, s.h, "systemctl", "is-active", serviceUnitName)
	if string(b) == unit && strings.TrimSpace(string(out)) == "active" {
		return ok("SimCore service is running; will restart it")
	}
	return ok("Will update and restart " + serviceUnitName)
}

func (s *Service) Apply(ctx context.Context, st *engine.State) engine.Result {
	fail := func(detail string) engine.Result { return block(engine.CodeSimCoreUnhealthy, detail) }
	if err := s.h.MkdirAll(s.p.DataDir, 0o700); err != nil {
		return fail("cannot create " + s.p.DataDir + ": " + err.Error())
	}
	unit := ServiceUnit(runInput(s.p, s.image, st))
	if err := s.h.WriteFile(ServiceUnitPath, []byte(unit), unitMode); err != nil {
		return fail("cannot write " + ServiceUnitPath + ": " + err.Error())
	}
	if _, stderr, err := runFor(ctx, s.h, commandTimeout, "systemctl", "daemon-reload"); err != nil {
		return fail("systemctl daemon-reload failed: " + failure(stderr, err))
	}
	if _, stderr, err := runFor(ctx, s.h, commandTimeout, "systemctl", "enable", serviceUnitName); err != nil {
		return fail("cannot enable " + serviceUnitName + ": " + failure(stderr, err))
	}
	// restart, not start: a oneshot unit that is already active would
	// otherwise keep the old container and its old settings.
	if _, stderr, err := runFor(ctx, s.h, unitTimeout, "systemctl", "restart", serviceUnitName); err != nil {
		return fail("SimCore did not start: " + failure(stderr, err) + "; see: journalctl -u " + serviceUnitName)
	}
	return ok("SimCore service started")
}

// ------------------------------------------------------------- HealthGate

// HealthGate is the last step of an install or upgrade (§9). It passes when
// the container's health check is healthy, /api/health reads cleanly with no
// MASTER_KEY_MISMATCH, and SimCore answers on the chosen address as well as on
// loopback. Degraded components other than api_auth do not stop it: the
// install completes with a warning naming them (FactHealthFaults).
type HealthGate struct {
	Clock
	h host.Host
}

func NewHealthGate(h host.Host) *HealthGate { return &HealthGate{Clock: realClock(), h: h} }

func (*HealthGate) ID() string    { return "health" }
func (*HealthGate) Title() string { return "Health" }

// Check is the dry run: SimCore is judged only after the service starts.
func (*HealthGate) Check(context.Context, *engine.State) engine.Result {
	return ok("Checked once SimCore has started")
}

func healthURL(hostname string, port int) string {
	return "http://" + net.JoinHostPort(hostname, strconv.Itoa(port)) + "/api/health"
}

func (s *HealthGate) Apply(ctx context.Context, st *engine.State) engine.Result {
	port := portOf(st)
	addr := st.Choices.Address

	state := "not found"
	if !s.waitUntil(ctx, healthWait, healthEvery, func() bool {
		out, _, err := docker(ctx, s.h, pollTimeout, "inspect", "--format", "{{.State.Health.Status}}", runtime.ContainerName)
		if err != nil {
			state = "not found"
			return false
		}
		state = strings.TrimSpace(string(out))
		return state == "healthy"
	}) {
		return block(engine.CodeSimCoreUnhealthy, fmt.Sprintf(
			"the SimCore container is %s, not healthy, after %s; see: %s logs %s",
			state, healthWait, strings.Join(dockerArgs(), " "), runtime.ContainerName))
	}

	// A listen-only SimCore does not answer on loopback; its own address is
	// then the local probe.
	local := healthURL("127.0.0.1", port)
	if st.Choices.ListenOnly {
		local = healthURL(addr, port)
	}
	status, body, err := s.h.HTTPGet(ctx, local)
	if err != nil {
		return block(engine.CodeSimCoreUnhealthy, "GET "+local+" failed: "+err.Error())
	}
	if status != 200 {
		return block(engine.CodeSimCoreUnhealthy, fmt.Sprintf("GET %s answered HTTP %d", local, status))
	}
	verdict, err := runtime.JudgeHealth(body)
	if err != nil {
		return block(engine.CodeSimCoreUnhealthy, "GET "+local+": "+err.Error())
	}
	if verdict.Code == engine.CodeMasterKeyMismatch {
		return block(engine.CodeMasterKeyMismatch,
			"the master secret does not open the stored credentials; restore the secret this database was created with")
	}

	public := healthURL(addr, port)
	if public != local {
		status, _, err := s.h.HTTPGet(ctx, public)
		switch {
		case err != nil:
			return block(engine.CodeAddressUnreachable, "SimCore answers on loopback but not at "+public+": "+err.Error())
		case status != 200:
			return block(engine.CodeAddressUnreachable, fmt.Sprintf("SimCore answers on loopback, but %s answered HTTP %d", public, status))
		}
	}

	if len(verdict.Faults) > 0 {
		st.Facts[FactHealthFaults] = strings.Join(verdict.Faults, ",")
		return engine.Result{Code: engine.CodeOK, Warning: true, Detail: "faults: " + strings.Join(verdict.Faults, ", ")}
	}
	delete(st.Facts, FactHealthFaults)
	return ok("SimCore is healthy at http://" + net.JoinHostPort(addr, strconv.Itoa(port)))
}
