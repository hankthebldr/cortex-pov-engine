// Package linux holds the installer's read-only Linux host checks (spec §7.1,
// §6.1). Every step here is check-only: Apply reports OK and changes nothing,
// and every read goes through host.Host so the steps run under host.Fake on
// any OS. What a step learns is published through State.Facts under the keys
// below, for the wizard to render.
package linux

// Fact keys written to engine.State.Facts. Values are single short strings;
// none ever holds a secret.
const (
	FactOS                 = "os"                  // pretty name, e.g. "Ubuntu 24.04.1 LTS"
	FactArch               = "arch"                // "amd64" or "arm64"
	FactCPUs               = "cpus"                // logical CPU count, decimal; absent when unreadable
	FactMemoryGiB          = "memory_gib"          // total RAM, whole GiB (rounded down)
	FactDiskFreeGiB        = "disk_free_gib"       // free space under /var/lib, whole GiB
	FactAddressCandidates  = "address_candidates"  // comma-joined, default-route interface first
	FactProxy              = "proxy"               // effective proxy URL; "" = none
	FactProxyPAC           = "proxy_pac"           // "true" when a PAC/WPAD setting was found
	FactEndpointProtection = "endpoint_protection" // "cortex-xdr" or "none"

	// FactFirewall is "ufw", "firewalld" or "none". The Task 7 checks do not
	// set it: the firewall step that opens the port owns the detection.
	FactFirewall = "firewall"
)
