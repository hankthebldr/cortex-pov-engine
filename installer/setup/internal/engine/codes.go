package engine

// Code is a stable result code. The CLI prints it, the wizard shows it on the
// row that raised it, and the setup log records it. The set below is the
// design's initial list (spec §13) verbatim; a later step may add a code but
// must never rename one, because operators and runbooks match on the text.
type Code string

const (
	CodeOK                        Code = "OK"
	CodeNotElevated               Code = "NOT_ELEVATED"
	CodeOSUnsupported             Code = "OS_UNSUPPORTED"
	CodeArchUnsupported           Code = "ARCH_UNSUPPORTED"
	CodeNoSystemd                 Code = "NO_SYSTEMD"
	CodeNoCgroupV2                Code = "NO_CGROUP_V2"
	CodeDiskLow                   Code = "DISK_LOW"
	CodeMemoryLow                 Code = "MEMORY_LOW"
	CodePortInUse                 Code = "PORT_IN_USE"
	CodeWinBuildUnsupported       Code = "WIN_BUILD_UNSUPPORTED"
	CodeWSL2NotAvailable          Code = "WSL2_NOT_AVAILABLE"
	CodeVirtualizationUnavailable Code = "VIRTUALIZATION_UNAVAILABLE"
	CodeWSLBlockedByPolicy        Code = "WSL_BLOCKED_BY_POLICY"
	CodeRebootRequired            Code = "REBOOT_REQUIRED"
	CodePayloadCorrupt            Code = "PAYLOAD_CORRUPT"
	CodeImageIDMismatch           Code = "IMAGE_ID_MISMATCH"
	CodeEngineStartFailed         Code = "ENGINE_START_FAILED"
	CodeSimCoreUnhealthy          Code = "SIMCORE_UNHEALTHY"
	CodeMasterKeyMismatch         Code = "MASTER_KEY_MISMATCH"
	CodeAddressUnreachable        Code = "ADDRESS_UNREACHABLE"
	CodeFirewallRuleFailed        Code = "FIREWALL_RULE_FAILED"
	CodeComposeDeploymentPresent  Code = "COMPOSE_DEPLOYMENT_PRESENT"
	CodeRunsInFlight              Code = "RUNS_IN_FLIGHT"
	CodeUpgradeRolledBack         Code = "UPGRADE_ROLLED_BACK"
	CodeDowngradeRefused          Code = "DOWNGRADE_REFUSED"
	CodeUninstallIncomplete       Code = "UNINSTALL_INCOMPLETE"

	// Raised as warnings (Result.Warning set): they explain something the
	// operator must know or do, and never stop an install.
	CodeProxyPACUnsupported               Code = "PROXY_PAC_UNSUPPORTED"
	CodeEndpointProtectionExclusionNeeded Code = "ENDPOINT_PROTECTION_EXCLUSION_NEEDED"

	// Added by the env-file step: a proxy URL carries credentials, which v1
	// does not support (spec §8.6). Blocking.
	CodeProxyAuthUnsupported Code = "PROXY_AUTH_UNSUPPORTED"

	// Added by the Linux address check: an address the operator gave is not an
	// IP literal or an RFC 1123 host name, listen-only was asked for with a
	// name, or nothing was given and nothing could be detected. Blocking.
	CodeAddressInvalid Code = "ADDRESS_INVALID"

	// Added by the Linux config step: the master secret or the env file could
	// not be created, or an existing secret file is not a valid secret (it is
	// never replaced). Blocking.
	CodeConfigWriteFailed Code = "CONFIG_WRITE_FAILED"

	// Added by the step engine: the run's context ended (Ctrl-C, the wizard
	// closing, a deadline) before the next phase started. Blocking.
	CodeCancelled Code = "CANCELLED"
)
