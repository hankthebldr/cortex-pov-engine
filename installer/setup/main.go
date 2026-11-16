// povengine-setup installs POVengine (SimCore) on a jumpbox: the wizard when
// run bare, or the same steps from the command line.
//
// Usage:
//
//	povengine-setup                      # wizard: elevate, serve, open the browser
//	povengine-setup install [flags]      # CLI, same steps; --yes takes every default
//	povengine-setup upgrade [flags]      # same as install when an install exists
//	povengine-setup uninstall [--purge]  # keeps data unless --purge (and confirms)
//	povengine-setup status               # read-only: what is installed, health, codes
//	povengine-setup check                # read-only: every step's check, nothing applied
//
// Exit codes: 0 done, 2 usage error, 3 not implemented yet. The commands are
// wired to their step plans as those land; until then they exit 3.
package main

import (
	"fmt"
	"io"
	"os"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/cli"
)

// version is set at build time: -ldflags "-X main.version=<v>".
var version = "dev"

const (
	exitOK             = 0
	exitUsage          = 2
	exitNotImplemented = 3
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	cmd, err := cli.Parse(args)
	if err != nil {
		fmt.Fprintf(stderr, "povengine-setup: %v\n\n%s", err, cli.UsageText())
		return exitUsage
	}
	switch {
	case cmd.Help:
		fmt.Fprint(stdout, cli.UsageText())
		return exitOK
	case cmd.Version:
		fmt.Fprintln(stdout, version)
		return exitOK
	}

	switch cmd.Name {
	case "", "serve", "check", "install", "upgrade", "uninstall", "status":
		name := cmd.Name
		if name == "" {
			name = "the wizard"
		}
		fmt.Fprintf(stderr, "povengine-setup: %s is not yet implemented\n", name)
		return exitNotImplemented
	default:
		// cli.Parse accepts no other name; reaching here is a parser bug.
		fmt.Fprintf(stderr, "povengine-setup: unknown command %q\n\n%s", cmd.Name, cli.UsageText())
		return exitUsage
	}
}
