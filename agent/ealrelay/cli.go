package ealrelay

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
)

// Exit codes. 0-2 are the EAL CLI's own (complete, not complete, usage);
// SimCore answers 3 for a plugin that cannot be relayed.
const (
	exitOK         = 0
	exitIncomplete = 1
	exitUsage      = 2
	// exitRelay: the call never reached SimCore, or SimCore refused it.
	exitRelay = 4
)

const (
	// maxCampaignBytes mirrors SimCore's limit, so an over-long file is
	// refused here with the file's name instead of there without it.
	maxCampaignBytes = 256 * 1024
	usage            = "usage: eal --relay <host:port> --ticket-file <path> " +
		"(run <campaign.yml> [--live] | describe <plugin> | list-plugins)"
	// maxTicketBytes is far more than a ticket; it only bounds the read.
	maxTicketBytes = 256
)

// Main is the `eal` subcommand: the beacon's stand-in for
// `python3 -m scripts.eal_simulator.cli`. args are everything after `eal`. It
// prints what SimCore's CLI would have printed, performs any operations
// SimCore returned, and returns the exit code.
func Main(args []string, stdout, stderr io.Writer) int {
	return run(args, stdout, stderr, &Runner{}, http.DefaultTransport.(*http.Transport).Clone())
}

func run(args []string, stdout, stderr io.Writer, runner *Runner, transport *http.Transport) int {
	relay, ticketFile, rest, err := splitGlobal(args)
	if err != nil {
		fmt.Fprintf(stderr, "error: %v\n%s\n", err, usage)
		return exitUsage
	}
	req, err := parseVerb(rest)
	if err != nil {
		fmt.Fprintf(stderr, "error: %v\n", err)
		return exitUsage
	}
	// The ticket is read from a file only this step's account can read. It is
	// never on a command line or in an environment.
	req.Ticket, err = readTicket(ticketFile)
	if err != nil {
		fmt.Fprintf(stderr, "EAL_RELAY_REFUSED: EAL_RELAY_TICKET_UNREADABLE: %v\n", err)
		return exitRelay
	}

	// The beacon is on loopback. No proxy is ever the way there.
	transport.Proxy = nil
	resp, err := ask(&http.Client{Transport: transport}, relay, req)
	if err != nil {
		var ref *Refusal
		if errors.As(err, &ref) {
			fmt.Fprintf(stderr, "EAL_RELAY_REFUSED: %s: %s\n", ref.Code, ref.Detail)
		} else {
			fmt.Fprintf(stderr, "EAL_RELAY_UNREACHABLE: %v\n", err)
		}
		return exitRelay
	}

	_, _ = io.WriteString(stdout, resp.Stdout)
	_, _ = io.WriteString(stderr, resp.Stderr)
	code := resp.ExitCode
	if len(resp.Ops) == 0 {
		return code
	}

	sum, err := runner.Run(context.Background(), resp.Ops, resp.Allowlist)
	if err != nil {
		// Nothing was sent: the plan was refused whole.
		fmt.Fprintf(stdout, "EAL_WIRE_OP_REFUSED: %v\n", err)
		return nonZero(code)
	}
	line, _ := json.MarshalIndent(map[string]Summary{"eal_wire": sum}, "", "  ")
	fmt.Fprintf(stdout, "%s\n", line)
	if sum.Network > 0 && sum.Reached == 0 {
		fmt.Fprintf(stdout, "EAL_WIRE_NOT_DELIVERED: 0 of %d operations reached a destination\n", sum.Network)
		return nonZero(code)
	}
	return code
}

func nonZero(code int) int {
	if code == exitOK {
		return exitIncomplete
	}
	return code
}

// splitGlobal takes --relay and --ticket-file off the front of args.
func splitGlobal(args []string) (relay, ticketFile string, rest []string, err error) {
	i := 0
flags:
	for i+1 < len(args) {
		switch args[i] {
		case "--relay":
			relay = args[i+1]
		case "--ticket-file":
			ticketFile = args[i+1]
		default:
			break flags
		}
		i += 2
	}
	if relay == "" || ticketFile == "" {
		return "", "", nil, errors.New("this command is run by the beacon for a step SimCore marked; it needs --relay and --ticket-file")
	}
	return relay, ticketFile, args[i:], nil
}

// readTicket reads the step's ticket from the file the beacon wrote for it.
// The file is gone once the step has ended.
func readTicket(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", errors.New("this step's ticket file cannot be read (the step has ended, or it runs as another account)")
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxTicketBytes))
	if err != nil || strings.TrimSpace(string(data)) == "" {
		return "", errors.New("this step's ticket file is empty")
	}
	return strings.TrimSpace(string(data)), nil
}

// parseVerb reads the CLI's own arguments: the same three verbs and flags
// scripts/eal_simulator/cli.py takes.
func parseVerb(args []string) (Request, error) {
	if len(args) == 0 {
		return Request{}, errors.New("the following arguments are required: command")
	}
	req := Request{Verb: args[0]}
	args = args[1:]
	switch req.Verb {
	case "list-plugins":
		return req, nil
	case "describe":
		if len(args) != 1 {
			return req, errors.New("describe takes one plugin name")
		}
		req.Plugin = args[0]
		return req, nil
	case "run":
		path := ""
		for i := 0; i < len(args); i++ {
			a := args[i]
			switch {
			case a == "--live":
				req.Live = true
			case a == "--audit-file":
				i++ // SimCore keeps the audit trail; the path is not used here
			case strings.HasPrefix(a, "--audit-file="):
			case strings.HasPrefix(a, "-"):
				return req, fmt.Errorf("unrecognized arguments: %s", a)
			case path == "":
				path = a
			default:
				return req, fmt.Errorf("unrecognized arguments: %s", a)
			}
		}
		if path == "" {
			return req, errors.New("the following arguments are required: campaign")
		}
		text, err := readCampaign(path)
		if err != nil {
			return req, err
		}
		req.CampaignYAML = text
		return req, nil
	}
	return req, fmt.Errorf("invalid choice: %q (choose from run, describe, list-plugins)", req.Verb)
}

func readCampaign(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("campaign file not found: %s", path)
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxCampaignBytes+1))
	if err != nil {
		return "", fmt.Errorf("campaign file could not be read: %s", path)
	}
	if len(data) > maxCampaignBytes {
		return "", fmt.Errorf("campaign file is over %d bytes: %s", maxCampaignBytes, path)
	}
	return string(data), nil
}

// ask sends the call to the beacon that started this step.
func ask(client *http.Client, relay string, req Request) (Response, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return Response{}, err
	}
	httpResp, err := client.Post("http://"+relay+"/relay", "application/json", bytes.NewReader(body))
	if err != nil {
		return Response{}, err
	}
	defer httpResp.Body.Close()
	data, err := io.ReadAll(httpResp.Body)
	if err != nil {
		return Response{}, err
	}
	if httpResp.StatusCode != http.StatusOK {
		return Response{}, ParseRefusal(httpResp.StatusCode, data)
	}
	var resp Response
	if err := json.Unmarshal(data, &resp); err != nil {
		return Response{}, fmt.Errorf("the beacon's answer is not JSON: %w", err)
	}
	return resp, nil
}
