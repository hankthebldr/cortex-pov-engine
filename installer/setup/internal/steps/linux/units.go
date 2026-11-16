package linux

import (
	"strings"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/runtime"
)

const (
	engineDir     = "/opt/povengine/engine"
	engineDockerd = engineDir + "/dockerd"

	containerdUnitName = "povengine-containerd.service"
	engineUnitName     = "povengine-engine.service"
	serviceUnitName    = "povengine.service"

	// ContainerdUnitPath, EngineUnitPath and ServiceUnitPath are where the
	// installer writes its three systemd units.
	ContainerdUnitPath = "/etc/systemd/system/" + containerdUnitName
	EngineUnitPath     = "/etc/systemd/system/" + engineUnitName
	ServiceUnitPath    = "/etc/systemd/system/" + serviceUnitName

	// daemonJSONPath is dockerd's --config-file. Without the flag dockerd
	// reads /etc/docker/daemon.json: a key there that the unit also passes as
	// a flag stops the engine from starting, and any other (log driver,
	// storage driver, userns remap, default runtime) would be inherited
	// silently. The installer's file is empty.
	daemonJSONPath = engineDir + "/daemon.json"
	daemonJSON     = "{}\n"

	// containerdConfigPath is the private containerd's --config. Everything
	// else is set by flag; the CRI plugin is off because nothing here is a
	// Kubernetes node.
	containerdConfigPath = engineDir + "/containerd.toml"
	containerdConfig     = "version = 2\ndisabled_plugins = [\"io.containerd.grpc.v1.cri\"]\n"
)

// unitHeader marks the units as the installer's: a re-run replaces them.
const unitHeader = "# Written by povengine-setup. Do not edit: the installer replaces this file.\n"

// ContainerdUnit is povengine-containerd.service: the bundled containerd,
// with its own socket, root, state and config. Without it dockerd would
// attach to a system containerd already running on the host (Docker CE, a
// containerd-based Kubernetes node), so SimCore's image would live in the
// customer's containerd: their prune could delete it, their restarts would
// take SimCore down, and an uninstall would leave its layers behind.
func ContainerdUnit() string {
	return unitHeader + `[Unit]
Description=POVengine private containerd

[Service]
Type=notify
Environment=PATH=/opt/povengine/engine:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/opt/povengine/engine/containerd --config /opt/povengine/engine/containerd.toml --address /run/povengine/containerd/containerd.sock --root /var/lib/povengine/containerd --state /run/povengine/containerd
Restart=always
RestartSec=2
Delegate=yes
KillMode=process
LimitNOFILE=infinity

[Install]
WantedBy=multi-user.target
`
}

// EngineUnit is povengine-engine.service: the private Docker engine (§7.1).
// Its own config file, socket, containerd (with its own namespaces), data
// root, exec root and pidfile keep it apart from a Docker engine and a
// containerd the customer already runs, and with no iptables or ip6tables
// rules, masquerade or bridge of its own it never touches the host firewall
// (ip6tables is on by default from Docker 27, and --iptables=false does not
// turn it off). --group root keeps the socket root's alone, even on a host
// whose docker group reaches the customer's own engine.
//
// PATH puts the bundled runc, docker-init and docker-proxy first, because
// dockerd starts them by name. Type=notify makes systemd wait until dockerd
// says it is ready, so povengine.service (After= this unit) never runs
// `docker run` against a socket that does not answer yet, at install or at
// boot. Delegate and KillMode=process follow Docker's own unit: the engine
// manages its containers' cgroups, and stopping it signals dockerd alone,
// which stops the containers itself.
//
// RuntimeDirectoryPreserve=yes: /run/povengine also holds the private
// containerd's socket and state (/run/povengine/containerd), so stopping or
// restarting the engine must not delete it under a containerd that is still
// running. dockerd replaces its own stale socket and pidfile on start, and
// /run is emptied at boot anyway.
func EngineUnit() string {
	return unitHeader + `[Unit]
Description=POVengine private container engine
Requires=povengine-containerd.service
After=povengine-containerd.service network-online.target
Wants=network-online.target

[Service]
Type=notify
Environment=PATH=/opt/povengine/engine:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/opt/povengine/engine/dockerd --config-file /opt/povengine/engine/daemon.json --host unix:///run/povengine/docker.sock --group root --containerd /run/povengine/containerd/containerd.sock --containerd-namespace povengine --containerd-plugins-namespace povengine-plugins --data-root /var/lib/povengine/engine --exec-root /run/povengine/exec --pidfile /run/povengine/dockerd.pid --iptables=false --ip6tables=false --ip-masq=false --bridge=none
RuntimeDirectory=povengine
RuntimeDirectoryPreserve=yes
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

// dockerArgs prefixes args with the private engine's CLI, its own config
// directory and its socket. --config keeps the CLI off root's
// ~/.docker/config.json, whose proxies `docker run` would otherwise copy into
// SimCore's environment (HTTP(S)_PROXY, NO_PROXY, FTP_PROXY, ALL_PROXY in both
// cases), overriding the installer's proxy choice and possibly routing the
// container's own health probe through a proxy.
func dockerArgs(args ...string) []string {
	return append([]string{ownEngineDocker, "--config", ownEngineCLIConfig, "-H", ownEngineSocket}, args...)
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
