// Package cli parses povengine-setup's command line into a Command. It does no
// host work and prints nothing; main decides what to run and what to print.
package cli

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"strconv"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
)

// ErrUsage is wrapped by every error Parse returns: an unknown command or
// flag, a bad value, or a flag given to a command that does not take it. main
// exits 2 on it, as scripts/dev-up.sh does on an unknown argument.
var ErrUsage = errors.New("usage error")

// DefaultPort is SimCore's port unless --port says otherwise.
const DefaultPort = 8888

// Command is a parsed command line.
type Command struct {
	// Name is "" (the wizard launcher), "serve", "check", "install",
	// "upgrade", "uninstall" or "status".
	Name        string
	Choices     engine.Choices
	Yes         bool   // take every default without asking
	URLFile     string // serve only: where the elevated server writes the wizard URL
	ConfirmHost string // uninstall only: the host name, confirming --purge
	Help        bool   // -h / --help: print the usage and exit 0
	Version     bool   // --version: print the version and exit 0
}

var commands = map[string]bool{
	"serve": true, "check": true, "install": true, "upgrade": true, "uninstall": true, "status": true,
}

// onlyWith names the flags one command alone accepts.
var onlyWith = map[string]string{
	"purge":        "uninstall",
	"confirm-host": "uninstall",
	"url-file":     "serve",
}

// Parse reads args (without the program name). Flags may come before or after
// the command, in Go's flag syntax (--port 9000, --port=9000, -port 9000).
func Parse(args []string) (Command, error) {
	cmd := Command{Choices: engine.Choices{Port: DefaultPort, Firewall: true}}
	var listen, proxy string

	fs := flag.NewFlagSet("povengine-setup", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.Usage = func() {}
	fs.StringVar(&cmd.Choices.Address, "address", "", "")
	fs.IntVar(&cmd.Choices.Port, "port", DefaultPort, "")
	fs.StringVar(&listen, "listen", "all", "")
	fs.Var(boolTo{&cmd.Choices.Firewall, true}, "firewall", "")
	fs.Var(boolTo{&cmd.Choices.Firewall, false}, "no-firewall", "")
	fs.StringVar(&proxy, "proxy", "", "")
	fs.BoolVar(&cmd.Choices.Purge, "purge", false, "")
	fs.StringVar(&cmd.ConfirmHost, "confirm-host", "", "")
	fs.BoolVar(&cmd.Yes, "yes", false, "")
	fs.StringVar(&cmd.URLFile, "url-file", "", "")
	fs.BoolVar(&cmd.Help, "help", false, "")
	fs.BoolVar(&cmd.Help, "h", false, "")
	fs.BoolVar(&cmd.Version, "version", false, "")

	// The flag package stops at the first positional argument: take it as the
	// command, then parse the flags that follow it into the same set.
	if err := fs.Parse(args); err != nil {
		return Command{}, usage("%v", err)
	}
	if rest := fs.Args(); len(rest) > 0 {
		cmd.Name = rest[0]
		if !commands[cmd.Name] {
			return Command{}, usage("unknown command %q", cmd.Name)
		}
		if err := fs.Parse(rest[1:]); err != nil {
			return Command{}, usage("%v", err)
		}
		if extra := fs.Args(); len(extra) > 0 {
			return Command{}, usage("unexpected argument %q after %s", extra[0], cmd.Name)
		}
	}
	if cmd.Help || cmd.Version {
		return cmd, nil
	}

	// Visit walks the flags actually given, in lexical order.
	set := map[string]bool{}
	var misplaced error
	fs.Visit(func(f *flag.Flag) {
		set[f.Name] = true
		if want, ok := onlyWith[f.Name]; ok && cmd.Name != want && misplaced == nil {
			misplaced = usage("--%s is accepted only with %s", f.Name, want)
		}
	})
	if misplaced != nil {
		return Command{}, misplaced
	}

	if cmd.Choices.Port < 1 || cmd.Choices.Port > 65535 {
		return Command{}, usage("--port must be between 1 and 65535, got %d", cmd.Choices.Port)
	}
	switch listen {
	case "all":
	case "address":
		cmd.Choices.ListenOnly = true
	default:
		return Command{}, usage("--listen takes all or address, got %q", listen)
	}
	if set["proxy"] {
		if err := checkProxy(proxy); err != nil {
			return Command{}, err
		}
		cmd.Choices.Proxy = proxy
	}
	return cmd, nil
}

// checkProxy accepts "none" or an absolute URL with a host
// (http://198.51.100.7:3128).
func checkProxy(p string) error {
	if p == "none" {
		return nil
	}
	u, err := url.Parse(p)
	if err != nil || u.Scheme == "" || u.Host == "" {
		return usage("--proxy takes a URL such as http://proxy.example:3128, or none; got %q", p)
	}
	return nil
}

func usage(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrUsage, fmt.Sprintf(format, args...))
}

// boolTo is a boolean flag that stores a fixed value into a shared target, so
// --firewall and --no-firewall write the same field and the later one wins.
type boolTo struct {
	target *bool
	value  bool
}

func (b boolTo) IsBoolFlag() bool { return true }

func (b boolTo) String() string {
	if b.target == nil {
		return ""
	}
	return strconv.FormatBool(*b.target == b.value)
}

func (b boolTo) Set(s string) error {
	on, err := strconv.ParseBool(s)
	if err != nil {
		return err
	}
	if on {
		*b.target = b.value
	} else {
		*b.target = !b.value
	}
	return nil
}

// UsageText is the help: the commands of spec §5.2 and the flags the CLI
// shares with the wizard. --url-file is the launcher's internal hand-off and
// is left out.
func UsageText() string {
	return `Usage:
  povengine-setup                      open the setup wizard in a browser
  povengine-setup install [flags]      install POVengine; --yes takes every default
  povengine-setup upgrade [flags]      upgrade the installed POVengine
  povengine-setup uninstall [--purge]  remove POVengine; keeps data unless --purge
  povengine-setup status               show what is installed and its health (read-only)
  povengine-setup check [flags]        run every check and change nothing (dry run)
  povengine-setup serve                run the wizard server only and print its URL

Flags:
  --address <host>        address beacons use to reach SimCore (default: detected)
  --port <n>              SimCore port (default 8888)
  --listen all|address    listen on every interface (default) or on --address only
  --firewall              open the port in the host firewall (default)
  --no-firewall           leave the host firewall alone
  --proxy <url>|none      egress proxy for SimCore (default: detected)
  --yes                   take every default without asking
  --purge                 uninstall only: also delete the data and the master secret
  --confirm-host <name>   uninstall only: this host's name, confirming --purge
  --version               print the version and exit
  -h, --help              show this help and exit
`
}
