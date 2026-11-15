package ealrelay

import (
	"context"
	"crypto/rand"
	"crypto/tls"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// Op is one primitive network operation SimCore rendered from a network-shape
// plugin step (core/eal_simulator/wire_plan.py). Which fields are read depends
// on Op: "http", "dns", "tcp" or "sleep".
type Op struct {
	Op string `json:"op"`

	// http
	Method          string            `json:"method,omitempty"`
	URL             string            `json:"url,omitempty"`
	Headers         map[string]string `json:"headers,omitempty"`
	BodyB64         string            `json:"body_b64,omitempty"`
	BodyRandomBytes int64             `json:"body_random_bytes,omitempty"`
	VerifyTLS       bool              `json:"verify_tls,omitempty"`

	// http and tcp
	TimeoutS float64 `json:"timeout_s,omitempty"`

	// dns: Resolver "" is the host's own resolver, else host:port.
	QName    string `json:"qname,omitempty"`
	QType    string `json:"qtype,omitempty"`
	Resolver string `json:"resolver,omitempty"`

	// tcp: Probe marks a connect-and-close sweep probe, where a closed port is
	// the expected answer and not a failed operation.
	Host   string  `json:"host,omitempty"`
	Port   int     `json:"port,omitempty"`
	Probe  bool    `json:"probe,omitempty"`
	Frames []Frame `json:"frames,omitempty"`

	// sleep
	Seconds float64 `json:"seconds,omitempty"`
}

// Frame is one step of a tcp op: send these bytes (if any), then wait up to
// Read seconds for a reply (if > 0). A reply is never required.
type Frame struct {
	SendB64 string  `json:"send_b64,omitempty"`
	Read    float64 `json:"read,omitempty"`
}

// What one step may be handed. SimCore enforces the same ceilings when it
// renders (wire_plan.py); the beacon does not take its word for it.
const (
	maxOps             = 4096
	maxSleepSeconds    = 600.0
	maxTotalSleep      = 3600.0
	maxBodyRandomBytes = int64(1) << 30
	maxTotalBodyBytes  = int64(4) << 30
	maxInlineBytes     = 1 << 20
	maxHTTPTimeout     = 3600.0
	maxTCPTimeout      = 60.0
	maxFrameRead       = 60.0
	dnsReplyWait       = 2 * time.Second
)

var qtypes = map[string]uint16{"A": 1, "NS": 2, "CNAME": 5, "MX": 15, "TXT": 16, "AAAA": 28}

// Summary is what the subcommand prints after performing a plan.
type Summary struct {
	Operations   int            `json:"operations"`
	Network      int            `json:"network_operations"`
	Reached      int            `json:"reached"`
	Failed       int            `json:"failed"`
	SleptSeconds float64        `json:"slept_seconds"`
	BytesSent    int64          `json:"bytes_sent"`
	ByKind       map[string]int `json:"by_kind"`
	Failures     []string       `json:"failures,omitempty"`
}

// maxListedFailures bounds Summary.Failures: a /24 sweep can fail 700 times.
const maxListedFailures = 20

// Runner performs operations. Every network touch goes through a field, so a
// test can stand in a fake network and no test contacts a real host.
type Runner struct {
	// HTTP sends one request. nil = a real client built for the op.
	HTTP func(req *http.Request, op Op) (*http.Response, error)
	// Dial opens a tcp or udp connection. nil = net.Dialer.
	Dial func(ctx context.Context, network, address string) (net.Conn, error)
	// Lookup asks the host's own resolver. nil = net.DefaultResolver.
	Lookup func(ctx context.Context, qtype, qname string) error
	// Sleep waits. nil = time.Sleep.
	Sleep func(time.Duration)
	// Rand fills random request bodies. nil = crypto/rand.
	Rand io.Reader
}

// Check refuses a plan the beacon will not perform: an operation whose
// destination is not in allowlist, an unknown operation, or one over a bound.
// It is called on the whole plan before anything is sent.
func Check(ops []Op, allowlist []string) error {
	if len(ops) > maxOps {
		return fmt.Errorf("%d operations, over the %d one step may be handed", len(ops), maxOps)
	}
	allowed := make(map[string]bool, len(allowlist))
	var zones []string
	for _, a := range allowlist {
		if z, ok := strings.CutPrefix(a, "dns:"); ok {
			zones = append(zones, strings.ToLower(strings.Trim(z, ".")))
			continue
		}
		allowed[strings.ToLower(a)] = true
	}
	var sleep float64
	var body int64
	for i, op := range ops {
		where := fmt.Sprintf("operation %d (%s)", i+1, op.Op)
		switch op.Op {
		case "sleep":
			if op.Seconds < 0 || op.Seconds > maxSleepSeconds {
				return fmt.Errorf("%s: a %gs sleep is outside 0..%gs", where, op.Seconds, maxSleepSeconds)
			}
			sleep += op.Seconds
		case "http":
			dest, err := httpDestination(op.URL)
			if err != nil {
				return fmt.Errorf("%s: %v", where, err)
			}
			if !allowed[dest] {
				return fmt.Errorf("%s: destination %s is outside the allowlist", where, dest)
			}
			if op.BodyRandomBytes < 0 || op.BodyRandomBytes > maxBodyRandomBytes {
				return fmt.Errorf("%s: a %d-byte body is over the limit", where, op.BodyRandomBytes)
			}
			if base64.StdEncoding.DecodedLen(len(op.BodyB64)) > maxInlineBytes+3 {
				return fmt.Errorf("%s: inline body over 1 MiB", where)
			}
			if op.TimeoutS < 0 || op.TimeoutS > maxHTTPTimeout {
				return fmt.Errorf("%s: timeout %gs is over the limit", where, op.TimeoutS)
			}
			body += op.BodyRandomBytes
		case "tcp":
			dest := strings.ToLower(op.Host) + ":" + strconv.Itoa(op.Port)
			if op.Host == "" || op.Port < 1 || op.Port > 65535 {
				return fmt.Errorf("%s: no usable host and port", where)
			}
			if !allowed[dest] {
				return fmt.Errorf("%s: destination %s is outside the allowlist", where, dest)
			}
			if op.TimeoutS < 0 || op.TimeoutS > maxTCPTimeout {
				return fmt.Errorf("%s: timeout %gs is over the limit", where, op.TimeoutS)
			}
			for _, f := range op.Frames {
				if base64.StdEncoding.DecodedLen(len(f.SendB64)) > maxInlineBytes+3 || f.Read < 0 || f.Read > maxFrameRead {
					return fmt.Errorf("%s: a frame is over the limit", where)
				}
			}
		case "dns":
			if _, ok := qtypes[strings.ToUpper(op.QType)]; !ok {
				return fmt.Errorf("%s: query type %q is not supported", where, op.QType)
			}
			name := strings.ToLower(strings.Trim(op.QName, "."))
			if !inZones(name, zones) {
				return fmt.Errorf("%s: name %s is outside the allowlist", where, name)
			}
			if op.Resolver != "" && !allowed[strings.ToLower(op.Resolver)] {
				return fmt.Errorf("%s: resolver %s is outside the allowlist", where, op.Resolver)
			}
		default:
			return fmt.Errorf("%s: unknown operation", where)
		}
	}
	if sleep > maxTotalSleep {
		return fmt.Errorf("%gs of sleep in all, over the %gs one step may wait", sleep, maxTotalSleep)
	}
	if body > maxTotalBodyBytes {
		return fmt.Errorf("%d bytes of request body in all, over the limit", body)
	}
	return nil
}

func inZones(name string, zones []string) bool {
	for _, z := range zones {
		if z != "" && (name == z || strings.HasSuffix(name, "."+z)) {
			return true
		}
	}
	return false
}

// httpDestination is the host:port a request to rawURL touches, in the form
// SimCore writes allowlist entries (the port is explicit or the scheme's).
func httpDestination(rawURL string) (string, error) {
	u, err := url.Parse(rawURL)
	if err != nil || u.Hostname() == "" {
		return "", errors.New("not a usable URL")
	}
	port := u.Port()
	if port == "" {
		switch strings.ToLower(u.Scheme) {
		case "http":
			port = "80"
		case "https":
			port = "443"
		default:
			return "", fmt.Errorf("scheme %q is not http or https", u.Scheme)
		}
	}
	return strings.ToLower(u.Hostname()) + ":" + port, nil
}

// Run checks the plan, then performs every operation in order. A plan that
// fails the check is not started: the error is returned and nothing is sent.
// An operation that fails (no route, refused, timed out) is counted and the
// plan carries on, as the plugin it was rendered from does.
func (r *Runner) Run(ctx context.Context, ops []Op, allowlist []string) (Summary, error) {
	sum := Summary{Operations: len(ops), ByKind: map[string]int{}}
	if err := Check(ops, allowlist); err != nil {
		return sum, err
	}
	for i, op := range ops {
		sum.ByKind[op.Op]++
		if op.Op == "sleep" {
			r.sleep(time.Duration(op.Seconds * float64(time.Second)))
			sum.SleptSeconds += op.Seconds
			continue
		}
		sum.Network++
		var sent int64
		var err error
		switch op.Op {
		case "http":
			sent, err = r.doHTTP(ctx, op)
		case "tcp":
			sent, err = r.doTCP(ctx, op)
		case "dns":
			sent, err = r.doDNS(ctx, op)
		}
		sum.BytesSent += sent
		if err != nil {
			sum.Failed++
			if len(sum.Failures) < maxListedFailures {
				sum.Failures = append(sum.Failures, fmt.Sprintf("operation %d (%s): %v", i+1, op.Op, err))
			}
			continue
		}
		sum.Reached++
	}
	return sum, nil
}

func (r *Runner) sleep(d time.Duration) {
	if r.Sleep != nil {
		r.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (r *Runner) dial(ctx context.Context, network, address string) (net.Conn, error) {
	if r.Dial != nil {
		return r.Dial(ctx, network, address)
	}
	var d net.Dialer
	return d.DialContext(ctx, network, address)
}

func seconds(s, fallback float64) time.Duration {
	if s <= 0 {
		s = fallback
	}
	return time.Duration(s * float64(time.Second))
}

// doHTTP sends one request. Any HTTP response counts as reaching the
// destination, whatever its status: the request is the signal.
func (r *Runner) doHTTP(ctx context.Context, op Op) (int64, error) {
	var body io.Reader
	var size int64
	switch {
	case op.BodyRandomBytes > 0:
		src := r.Rand
		if src == nil {
			src = rand.Reader
		}
		body, size = io.LimitReader(src, op.BodyRandomBytes), op.BodyRandomBytes
	case op.BodyB64 != "":
		raw, err := base64.StdEncoding.DecodeString(op.BodyB64)
		if err != nil {
			return 0, fmt.Errorf("body is not base64: %w", err)
		}
		body, size = strings.NewReader(string(raw)), int64(len(raw))
	}
	ctx, cancel := context.WithTimeout(ctx, seconds(op.TimeoutS, 30))
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, op.Method, op.URL, body)
	if err != nil {
		return 0, err
	}
	if size > 0 {
		req.ContentLength = size
	}
	for k, v := range op.Headers {
		req.Header.Set(k, v)
	}
	do := r.HTTP
	if do == nil {
		do = r.realHTTP
	}
	resp, err := do(req, op)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	return size + int64(len(op.URL)), nil
}

// realHTTP sends through a client built for this op. A redirect is not
// followed: its target is not a destination anybody allowlisted.
func (r *Runner) realHTTP(req *http.Request, op Op) (*http.Response, error) {
	client := &http.Client{
		Transport: &http.Transport{
			Proxy:             http.ProxyFromEnvironment,
			DialContext:       r.dial,
			TLSClientConfig:   &tls.Config{InsecureSkipVerify: !op.VerifyTLS}, // #nosec G402 -- the campaign's verify_tls, as the plugin
			DisableKeepAlives: true,
		},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	return client.Do(req)
}

// doTCP connects, walks the frames, and closes. For a probe, an attempt that
// is refused or times out still counts: the attempt is the signal.
func (r *Runner) doTCP(ctx context.Context, op Op) (int64, error) {
	timeout := seconds(op.TimeoutS, 5)
	dctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	conn, err := r.dial(dctx, "tcp", net.JoinHostPort(op.Host, strconv.Itoa(op.Port)))
	if err != nil {
		if op.Probe {
			return 0, nil
		}
		return 0, err
	}
	defer conn.Close()
	var sent int64
	buf := make([]byte, 4096)
	for _, f := range op.Frames {
		if f.SendB64 != "" {
			raw, err := base64.StdEncoding.DecodeString(f.SendB64)
			if err != nil {
				return sent, fmt.Errorf("frame is not base64: %w", err)
			}
			_ = conn.SetWriteDeadline(time.Now().Add(timeout))
			n, err := conn.Write(raw)
			sent += int64(n)
			if err != nil {
				return sent, err
			}
		}
		if f.Read > 0 {
			_ = conn.SetReadDeadline(time.Now().Add(seconds(f.Read, 0)))
			_, _ = conn.Read(buf) // a reply is welcome, never required
		}
	}
	return sent, nil
}

// doDNS sends one query. With a named resolver it is one UDP packet to that
// resolver, exactly the name and type asked for. Without one the host's own
// resolver is asked. No answer is needed either way.
func (r *Runner) doDNS(ctx context.Context, op Op) (int64, error) {
	qtype := strings.ToUpper(op.QType)
	if op.Resolver == "" {
		lookup := r.Lookup
		if lookup == nil {
			lookup = systemLookup
		}
		return int64(len(op.QName)), lookup(ctx, qtype, op.QName)
	}
	packet, err := dnsQuery(op.QName, qtypes[qtype])
	if err != nil {
		return 0, err
	}
	dctx, cancel := context.WithTimeout(ctx, dnsReplyWait)
	defer cancel()
	conn, err := r.dial(dctx, "udp", op.Resolver)
	if err != nil {
		return 0, err
	}
	defer conn.Close()
	if _, err := conn.Write(packet); err != nil {
		return 0, err
	}
	_ = conn.SetReadDeadline(time.Now().Add(dnsReplyWait))
	_, _ = conn.Read(make([]byte, 4096))
	return int64(len(packet)), nil
}

// systemLookup asks the host's resolver. NXDOMAIN, or any other answer the
// resolver gives, means the query was sent, which is all that is needed.
func systemLookup(ctx context.Context, qtype, qname string) error {
	var err error
	res := net.DefaultResolver
	switch qtype {
	case "TXT":
		_, err = res.LookupTXT(ctx, qname)
	case "MX":
		_, err = res.LookupMX(ctx, qname)
	case "NS":
		_, err = res.LookupNS(ctx, qname)
	case "CNAME":
		_, err = res.LookupCNAME(ctx, qname)
	case "AAAA":
		_, err = res.LookupIP(ctx, "ip6", qname)
	default:
		_, err = res.LookupIP(ctx, "ip4", qname)
	}
	var dnsErr *net.DNSError
	if errors.As(err, &dnsErr) {
		return nil
	}
	return err
}

// dnsQuery builds a standard recursive query for one name and type.
func dnsQuery(qname string, qtype uint16) ([]byte, error) {
	var id [2]byte
	if _, err := rand.Read(id[:]); err != nil {
		return nil, err
	}
	packet := []byte{id[0], id[1], 0x01, 0x00, 0x00, 0x01, 0, 0, 0, 0, 0, 0}
	for _, label := range strings.Split(strings.Trim(qname, "."), ".") {
		if label == "" || len(label) > 63 {
			return nil, fmt.Errorf("name %q has an empty or over-long label", qname)
		}
		packet = append(packet, byte(len(label)))
		packet = append(packet, label...)
	}
	packet = append(packet, 0)
	packet = binary.BigEndian.AppendUint16(packet, qtype)
	packet = binary.BigEndian.AppendUint16(packet, 1) // class IN
	return packet, nil
}
