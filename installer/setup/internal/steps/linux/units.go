package linux

import (
	"strings"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/runtime"
)

const (
	engineDir     = "/opt/povengine/engine"
	engineDockerd = engineDir + "/dockerd"

	engineUnitName  = "povengine-engine.service"
	serviceUnitName = "povengine.service"

	// EngineUnitPath and ServiceUnitPath are where the installer writes its
	// two systemd units.
	EngineUnitPath  = "/etc/systemd/system/" + engineUnitName
	ServiceUnitPath = "/etc/systemd/system/" + serviceUnitName
)

// unitHeader marks both units as the installer's: a re-run replaces them.
const unitHeader = "# Written by povengine-setup. Do not edit: the installer replaces this file.\n"

// EngineUnit is povengine-engine.service: the private Docker engine (§7.1).
// Its own socket, data root, exec root and pidfile keep it apart from a
// Docker engine the customer already runs, and with no iptables, masquerade
// or bridge of its own it cannot change the host's network.
//
// PATH puts the bundled containerd and runc first, because dockerd starts
// them by name. Type=notify makes systemd wait until dockerd says it is ready,
// so povengine.service (After= this unit) never runs `docker run` against a
// socket that does not answer yet, at install or at boot. Delegate and
// KillMode=process follow Docker's own unit: the engine manages its
// containers' cgroups, and stopping it signals dockerd alone, which stops the
// containers itself.
func EngineUnit() string {
	return unitHeader + `[Unit]
Description=POVengine private container engine
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
Environment=PATH=/opt/povengine/engine:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/opt/povengine/engine/dockerd --host unix:///run/povengine/docker.sock --data-root /var/lib/povengine/engine --exec-root /run/povengine/exec --pidfile /run/povengine/dockerd.pid --iptables=false --ip-masq=false --bridge=none
RuntimeDirectory=povengine
Restart=always
RestartSec=2
Delegate=yes
KillMode=process
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
`
}

// ServiceUnit is povengine.service: SimCore on the private engine (§7.1). Each
// start removes any old container and runs a fresh one from runtime.RunArgs,
// so a changed image, env file or address takes effect on the next start; the
// container's own --restart unless-stopped covers crashes in between.
func ServiceUnit(in runtime.RunInput) string {
	name := in.Name
	if name == "" {
		name = runtime.ContainerName
	}
	var b strings.Builder
	b.WriteString(unitHeader)
	b.WriteString(`[Unit]
Description=POVengine SimCore
Requires=` + engineUnitName + `
After=` + engineUnitName + `

[Service]
Type=oneshot
RemainAfterExit=yes
`)
	b.WriteString("ExecStartPre=-" + systemdCommand(dockerArgs("rm", "-f", name)) + "\n")
	b.WriteString("ExecStart=" + systemdCommand(dockerArgs(runtime.RunArgs(in)...)) + "\n")
	b.WriteString("ExecStop=" + systemdCommand(dockerArgs("stop", "-t", "15", name)) + "\n")
	b.WriteString(`
[Install]
WantedBy=multi-user.target
`)
	return b.String()
}

// dockerArgs prefixes args with the private engine's CLI and socket.
func dockerArgs(args ...string) []string {
	return append([]string{ownEngineDocker, "-H", ownEngineSocket}, args...)
}

// systemdCommand joins argv into one Exec*= line.
func systemdCommand(argv []string) string {
	q := make([]string, len(argv))
	for i, a := range argv {
		q[i] = systemdQuote(a)
	}
	return strings.Join(q, " ")
}

var systemdEscaper = strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", `\n`, "%", "%%", "$", "$$")

// systemdQuote writes one Exec*= argument: systemd expands %specifiers and
// $VARIABLES on these lines, so % and $ are doubled, and an argument holding
// whitespace, a quote or a backslash is double-quoted with C escapes (a
// newline becomes \n, so it cannot end the line).
func systemdQuote(a string) string {
	esc := systemdEscaper.Replace(a)
	if a == "" || strings.ContainsAny(a, " \t\n'\"\\") {
		return `"` + esc + `"`
	}
	return esc
}
