// Package config holds what the setup program writes for SimCore: the fixed
// install paths, the master secret and the env file.
package config

// Paths are the fixed locations of one install (design §8.1).
type Paths struct {
	Program    string // the setup program's own install directory
	ConfigDir  string // secret, env file and state file; admin-only
	SecretFile string
	EnvFile    string
	DataDir    string // SQLite database (inside the host distribution on Windows)
	EngineData string // the private engine's data (inside the distribution on Windows)
	Backups    string
	Logs       string
	StateFile  string
}

// DefaultPaths returns the paths for goos ("windows"; anything else is read
// as Linux). Windows paths are written with backslashes whatever OS this runs
// on, so tests on Linux or macOS see what a Windows host would. DataDir and
// EngineData on Windows are paths inside the host distribution, so they stay
// POSIX.
func DefaultPaths(goos string) Paths {
	if goos == "windows" {
		cfg := `C:\ProgramData\POVengine\config`
		return Paths{
			Program:    `C:\Program Files\POVengine`,
			ConfigDir:  cfg,
			SecretFile: cfg + `\secret`,
			EnvFile:    cfg + `\simcore.env`,
			DataDir:    "/var/lib/povengine/data",
			EngineData: "/var/lib/povengine/engine",
			Backups:    `C:\ProgramData\POVengine\backups`,
			Logs:       `C:\ProgramData\POVengine\logs`,
			StateFile:  cfg + `\setup-state.json`,
		}
	}
	cfg := "/etc/povengine"
	return Paths{
		Program:    "/opt/povengine",
		ConfigDir:  cfg,
		SecretFile: cfg + "/secret",
		EnvFile:    cfg + "/simcore.env",
		DataDir:    "/var/lib/povengine/data",
		EngineData: "/var/lib/povengine/engine",
		Backups:    "/var/lib/povengine/backups",
		Logs:       "/var/log/povengine",
		StateFile:  cfg + "/setup-state.json",
	}
}
