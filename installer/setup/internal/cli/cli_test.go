package cli

import (
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/hankthebldr/cortexsim/installer/setup/internal/engine"
)

func TestParse_UnknownFlagIsUsageError(t *testing.T) {
	for _, args := range [][]string{
		{"install", "--frobnicate"},
		{"--frobnicate"},
		{"frobnicate"},           // unknown command
		{"install", "extra"},     // a second positional argument
		{"install", "uninstall"}, // two commands
	} {
		_, err := Parse(args)
		if !errors.Is(err, ErrUsage) {
			t.Errorf("Parse(%q) error = %v, want ErrUsage", args, err)
		}
	}
}

func TestParse_InstallFlags(t *testing.T) {
	got, err := Parse(strings.Fields("install --address 192.0.2.10 --port 9000 --listen address --no-firewall --proxy none --yes"))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	want := Command{
		Name: "install",
		Choices: engine.Choices{
			Address:    "192.0.2.10",
			Port:       9000,
			ListenOnly: true,
			Firewall:   false,
			Proxy:      "none",
		},
		Yes: true,
	}
	if !reflect.DeepEqual(got, want) {
		t.Errorf("Parse = %+v\nwant  %+v", got, want)
	}

	// Defaults: port 8888, every interface, firewall rule on, detected proxy.
	got, err = Parse([]string{"install"})
	if err != nil {
		t.Fatalf("Parse(install): %v", err)
	}
	if want := (engine.Choices{Port: 8888, Firewall: true}); got.Choices != want {
		t.Errorf("defaults = %+v, want %+v", got.Choices, want)
	}

	// --yes and the choice flags may come before the command, and the
	// later of --firewall / --no-firewall wins.
	got, err = Parse(strings.Fields("--yes upgrade --no-firewall --firewall --proxy http://198.51.100.7:3128 --listen all"))
	if err != nil {
		t.Fatalf("Parse(--yes upgrade ...): %v", err)
	}
	if got.Name != "upgrade" || !got.Yes || !got.Choices.Firewall || got.Choices.ListenOnly || got.Choices.Proxy != "http://198.51.100.7:3128" {
		t.Errorf("Parse = %+v", got)
	}
}

func TestParse_PurgeOnlyWithUninstall(t *testing.T) {
	for _, args := range [][]string{
		{"install", "--purge"},
		{"--purge"},
		{"check", "--confirm-host", "jumpbox01"},
		{"status", "--confirm-host=jumpbox01"},
	} {
		if _, err := Parse(args); !errors.Is(err, ErrUsage) {
			t.Errorf("Parse(%q) error = %v, want ErrUsage", args, err)
		}
	}

	got, err := Parse(strings.Fields("uninstall --purge --confirm-host jumpbox01"))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if got.Name != "uninstall" || !got.Choices.Purge || got.ConfirmHost != "jumpbox01" {
		t.Errorf("Parse = %+v, want uninstall with Purge and ConfirmHost jumpbox01", got)
	}

	got, err = Parse([]string{"uninstall"})
	if err != nil || got.Choices.Purge {
		t.Errorf("Parse(uninstall) = %+v, %v; want Purge false: data is kept by default", got, err)
	}
}

func TestParse_URLFileOnlyWithServe(t *testing.T) {
	got, err := Parse(strings.Fields("serve --url-file /run/user/1000/povengine-url"))
	if err != nil {
		t.Fatalf("Parse: %v", err)
	}
	if got.Name != "serve" || got.URLFile != "/run/user/1000/povengine-url" {
		t.Errorf("Parse = %+v", got)
	}
	if _, err := Parse(strings.Fields("install --url-file /tmp/x")); !errors.Is(err, ErrUsage) {
		t.Errorf("--url-file with install: error = %v, want ErrUsage", err)
	}
	if strings.Contains(UsageText(), "url-file") {
		t.Error("--url-file is internal to the launcher and must not appear in the help")
	}
}

func TestParse_BadValuesAreUsageErrors(t *testing.T) {
	for _, args := range [][]string{
		{"install", "--port", "0"},
		{"install", "--port", "65536"},
		{"install", "--port", "eighty"},
		{"install", "--listen", "loopback"},
		{"install", "--proxy", ""},
		{"install", "--proxy", "proxy.corp.example:3128"}, // no scheme
		{"install", "--address"},                          // missing value
	} {
		if _, err := Parse(args); !errors.Is(err, ErrUsage) {
			t.Errorf("Parse(%q) error = %v, want ErrUsage", args, err)
		}
	}
}

func TestParse_VersionAndHelp(t *testing.T) {
	got, err := Parse([]string{"--version"})
	if err != nil || !got.Version || got.Name != "" {
		t.Errorf("Parse(--version) = %+v, %v", got, err)
	}
	for _, args := range [][]string{{"--help"}, {"-h"}, {"install", "--help"}} {
		got, err := Parse(args)
		if err != nil || !got.Help {
			t.Errorf("Parse(%q) = %+v, %v; want Help", args, got, err)
		}
	}
}
