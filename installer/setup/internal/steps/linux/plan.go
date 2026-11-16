package linux

import (
	"os"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/config"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
	"github.com/hankthebldr/cortexsim/installer/setup/internal/host"
)

// PlanInput is what an install needs from the package.
type PlanInput struct {
	ImageRef      string // the SimCore image reference the package loads
	ImageID       string // the image id the package pins
	ImagePath     string // the image archive (`docker load -i`)
	EngineArchive string // the static engine .tgz
	EngineVersion string // the engine version that archive holds
}

// InstallPlan is the Linux install, in order: the read-only checks, then the
// engine, the config, the image, the service, the firewall and the health
// gate. The proxy check reads this process's environment.
func InstallPlan(h host.Host, p config.Paths, in PlanInput) []engine.Step {
	return []engine.Step{
		NewHostCheck(h),
		NewPortFree(h),
		NewComposeAbsent(h),
		NewAddressStep(h),
		NewProxyStep(h, os.Environ()),
		NewEndpointProtection(h),
		NewEngine(h, in.EngineArchive, in.EngineVersion),
		NewConfig(h, p),
		NewImage(h, in.ImageRef, in.ImageID, in.ImagePath),
		NewService(h, p, in.ImageRef),
		NewFirewall(h),
		NewHealthGate(h),
	}
}
