// Package runtime builds the SimCore container's command line and judges its
// /api/health answer. It runs nothing itself: the platform steps (Linux
// systemd units, the Windows host distribution) pass RunArgs to the private
// engine's docker CLI and feed the health body to JudgeHealth.
package runtime

import (
	"net"
	"strconv"
)

const (
	// ContainerName is the one SimCore container an install runs.
	ContainerName = "povengine-simcore"
	// DefaultPort is the port the image's own HEALTHCHECK and CMD use.
	DefaultPort = 8888
)

// RunInput is what the SimCore container is started from.
type RunInput struct {
	Image, Name, EnvFile, DataDir string // Name "" = ContainerName
	Address                       string // the address beacons use; the bind address when ListenOnly
	Port                          int    // 0 = DefaultPort
	ListenOnly                    bool
}

// RunArgs returns the arguments after `docker -H unix:///run/povengine/docker.sock`
// that start SimCore: host networking (beacons' real source addresses reach
// SimCore, and the container shares the jumpbox's host name), restart on
// crash, the installer's env file and the data bind mount. --pull never makes
// a missing image fail at once instead of reaching for a registry: the
// install works offline, and a default-deny network would only hang the pull.
//
// The image's HEALTHCHECK and CMD listen on 0.0.0.0:8888 and probe
// 127.0.0.1:8888. When the operator asks to listen on one address, or for
// another port, both are overridden to match, because a probe of a port or
// address uvicorn does not listen on would mark a working SimCore unhealthy.
func RunArgs(in RunInput) []string {
	name := in.Name
	if name == "" {
		name = ContainerName
	}
	port := in.Port
	if port == 0 {
		port = DefaultPort
	}
	args := []string{
		"run", "-d",
		"--name", name,
		"--network", "host",
		"--restart", "unless-stopped",
		"--env-file", in.EnvFile,
		"-v", in.DataDir + ":/app/data",
		"--pull", "never",
	}
	if !in.ListenOnly && port == DefaultPort {
		return append(args, in.Image)
	}

	bind, probe := "0.0.0.0", "127.0.0.1"
	if in.ListenOnly {
		bind, probe = in.Address, in.Address
	}
	p := strconv.Itoa(port)
	health := "curl -fsS --max-time 5 -o /dev/null http://" + net.JoinHostPort(probe, p) + "/api/health"
	args = append(args, "--health-cmd", health, in.Image)
	return append(args,
		"uvicorn", "main:app",
		"--host", bind,
		"--port", p,
		"--workers", "1",
		"--timeout-graceful-shutdown", "5",
	)
}
