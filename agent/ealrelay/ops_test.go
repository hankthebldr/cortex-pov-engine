package ealrelay

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httptrace"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// specCases is spec/eal_wire_ops_cases.json: the operations SimCore's
// renderers are pinned to produce (tests/eal_simulator/test_wire_plan.py reads
// the same file). The beacon must perform exactly these, in this order.
type specCase struct {
	Name      string   `json:"name"`
	Plugin    string   `json:"plugin"`
	Ops       []Op     `json:"ops"`
	Allowlist []string `json:"allowlist"`
}

func loadSpecCases(t *testing.T) []specCase {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "spec", "eal_wire_ops_cases.json"))
	if err != nil {
		t.Fatalf("read spec cases: %v", err)
	}
	var doc struct {
		Cases []specCase `json:"cases"`
	}
	if err := json.Unmarshal(data, &doc); err != nil {
		t.Fatalf("decode spec cases: %v", err)
	}
	if len(doc.Cases) == 0 {
		t.Fatal("spec file has no cases")
	}
	return doc.Cases
}

// fakeNet stands in for the network: it records what each operation would put
// on the wire and never opens a socket.
type fakeNet struct {
	mu    sync.Mutex
	trace []string
}

func (f *fakeNet) add(format string, args ...interface{}) {
	f.mu.Lock()
	f.trace = append(f.trace, fmt.Sprintf(format, args...))
	f.mu.Unlock()
}

func (f *fakeNet) runner() *Runner {
	return &Runner{
		HTTP: func(req *http.Request, op Op) (*http.Response, error) {
			n := int64(0)
			if req.Body != nil {
				n, _ = io.Copy(io.Discard, req.Body)
			}
			headers := make([]string, 0, len(op.Headers))
			for k := range op.Headers {
				headers = append(headers, k+"="+req.Header.Get(k))
			}
			f.add("http %s %s body=%d headers=%d", req.Method, req.URL.String(), n, len(headers))
			for k, v := range op.Headers {
				if req.Header.Get(k) != v {
					f.add("MISSING HEADER %s", k)
				}
			}
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(""))}, nil
		},
		Dial: func(_ context.Context, network, address string) (net.Conn, error) {
			return &fakeConn{net: f, label: network + " " + address}, nil
		},
		Lookup: func(_ context.Context, qtype, qname string) error {
			f.add("dns %s %s via host resolver", qtype, qname)
			return nil
		},
		Sleep: func(d time.Duration) { f.add("sleep %s", d) },
		Rand:  zeroes{},
	}
}

type zeroes struct{}

func (zeroes) Read(p []byte) (int, error) {
	for i := range p {
		p[i] = 0
	}
	return len(p), nil
}

// fakeConn records each write and answers every read with a timeout, as a
// silent sinkhole would.
type fakeConn struct {
	net   *fakeNet
	label string
	wrote bool
}

func (c *fakeConn) Write(p []byte) (int, error) {
	c.wrote = true
	c.net.add("%s <- %s", c.label, base64.StdEncoding.EncodeToString(p))
	return len(p), nil
}
func (c *fakeConn) Read([]byte) (int, error) { return 0, os.ErrDeadlineExceeded }
func (c *fakeConn) Close() error {
	if !c.wrote {
		c.net.add("%s (connect only)", c.label)
	}
	return nil
}
func (c *fakeConn) LocalAddr() net.Addr              { return &net.TCPAddr{} }
func (c *fakeConn) RemoteAddr() net.Addr             { return &net.TCPAddr{} }
func (c *fakeConn) SetDeadline(time.Time) error      { return nil }
func (c *fakeConn) SetReadDeadline(time.Time) error  { return nil }
func (c *fakeConn) SetWriteDeadline(time.Time) error { return nil }

// expectedTrace is what a plan should put on the wire, derived from the ops
// alone, in order.
func expectedTrace(t *testing.T, ops []Op) []string {
	t.Helper()
	var out []string
	for _, op := range ops {
		switch op.Op {
		case "sleep":
			out = append(out, fmt.Sprintf("sleep %s", time.Duration(op.Seconds*float64(time.Second))))
		case "http":
			out = append(out, fmt.Sprintf("http %s %s body=%d headers=%d",
				op.Method, op.URL, op.BodyRandomBytes, len(op.Headers)))
		case "dns":
			if op.Resolver == "" {
				out = append(out, fmt.Sprintf("dns %s %s via host resolver", op.QType, op.QName))
				continue
			}
			out = append(out, "udp "+op.Resolver+" <- QUERY "+op.QType+" "+op.QName)
		case "tcp":
			label := "tcp " + net.JoinHostPort(op.Host, strconv.Itoa(op.Port))
			sent := false
			for _, f := range op.Frames {
				if f.SendB64 != "" {
					out = append(out, label+" <- "+f.SendB64)
					sent = true
				}
			}
			if !sent {
				out = append(out, label+" (connect only)")
			}
		default:
			t.Fatalf("spec case carries an operation this test does not know: %q", op.Op)
		}
	}
	return out
}

// readableQueries rewrites the raw DNS packets in a trace as "QUERY type
// name", so the comparison is on what was asked, not on the random query id.
func readableQueries(t *testing.T, trace []string) []string {
	t.Helper()
	out := make([]string, len(trace))
	for i, line := range trace {
		out[i] = line
		head, payload, ok := strings.Cut(line, " <- ")
		if !ok || !strings.HasPrefix(head, "udp ") {
			continue
		}
		raw, err := base64.StdEncoding.DecodeString(payload)
		if err != nil {
			t.Fatalf("udp payload is not base64: %v", err)
		}
		name, qtype := parseQuery(t, raw)
		out[i] = head + " <- QUERY " + qtype + " " + name
	}
	return out
}

func parseQuery(t *testing.T, packet []byte) (name, qtype string) {
	t.Helper()
	if len(packet) < 17 || packet[2] != 0x01 || packet[5] != 1 {
		t.Fatalf("not a one-question recursive query: % x", packet)
	}
	var labels []string
	i := 12
	for packet[i] != 0 {
		n := int(packet[i])
		labels = append(labels, string(packet[i+1:i+1+n]))
		i += 1 + n
	}
	code := uint16(packet[i+1])<<8 | uint16(packet[i+2])
	for k, v := range qtypes {
		if v == code {
			qtype = k
		}
	}
	return strings.Join(labels, "."), qtype
}

func TestOpsExecutedInOrderFromSpecCases(t *testing.T) {
	for _, tc := range loadSpecCases(t) {
		t.Run(tc.Name, func(t *testing.T) {
			fake := &fakeNet{}
			sum, err := fake.runner().Run(context.Background(), tc.Ops, tc.Allowlist)
			if err != nil {
				t.Fatalf("the pinned plan was refused: %v", err)
			}
			got, want := readableQueries(t, fake.trace), expectedTrace(t, tc.Ops)
			if !reflect.DeepEqual(got, want) {
				t.Errorf("operations were not performed as pinned\n got:\n  %s\nwant:\n  %s",
					strings.Join(got, "\n  "), strings.Join(want, "\n  "))
			}
			if sum.Operations != len(tc.Ops) || sum.NotSent != 0 || sum.Sent != sum.Network {
				t.Errorf("summary does not account for every operation: %+v", sum)
			}
		})
	}
}

func TestOpOutsideAllowlistRefused(t *testing.T) {
	inside := Op{Op: "http", Method: "GET", URL: "http://c2.example.test:8080/checkin"}
	allow := []string{"c2.example.test:8080", "dns:exfil.example.test", "192.0.2.53:53"}
	cases := map[string]Op{
		"another host":         {Op: "http", Method: "GET", URL: "http://elsewhere.example.test:8080/"},
		"another port":         {Op: "http", Method: "GET", URL: "http://c2.example.test:9090/"},
		"the scheme's port":    {Op: "http", Method: "GET", URL: "https://c2.example.test/"},
		"a tcp destination":    {Op: "tcp", Host: "192.0.2.9", Port: 445},
		"a name off the zone":  {Op: "dns", QName: "x.other.example.test", QType: "A"},
		"a look-alike zone":    {Op: "dns", QName: "x.notexfil.example.test", QType: "A"},
		"an unnamed resolver":  {Op: "dns", QName: "x.exfil.example.test", QType: "TXT", Resolver: "198.51.100.1:53"},
		"an unknown operation": {Op: "exec"},
		"a sleep past the cap": {Op: "sleep", Seconds: 86400},
	}
	for name, bad := range cases {
		t.Run(name, func(t *testing.T) {
			fake := &fakeNet{}
			_, err := fake.runner().Run(context.Background(), []Op{inside, bad, inside}, allow)
			if err == nil {
				t.Fatal("the plan was performed")
			}
			// Refused whole: the operation before the bad one was not sent either.
			if len(fake.trace) != 0 {
				t.Errorf("something was sent from a refused plan: %v", fake.trace)
			}
		})
	}
	// The same plan without the bad operation runs.
	fake := &fakeNet{}
	if _, err := fake.runner().Run(context.Background(), []Op{inside}, allow); err != nil || len(fake.trace) != 1 {
		t.Fatalf("an allowed plan did not run: err=%v trace=%v", err, fake.trace)
	}
}

// The three tests below use the real client, dialer and sockets, against
// listeners on loopback only.

func TestHTTPOpSendsTheRequestAsRendered(t *testing.T) {
	type seen struct {
		method, path, ua, runID string
		body                    int
	}
	got := make(chan seen, 1)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		got <- seen{r.Method, r.URL.RequestURI(), r.Header.Get("User-Agent"),
			r.Header.Get("X-Simulation-Run-ID"), len(body)}
		w.WriteHeader(http.StatusNotFound) // any answer is an answer
	}))
	defer srv.Close()
	host := strings.TrimPrefix(srv.URL, "http://")

	op := Op{Op: "http", Method: "POST", URL: srv.URL + "/checkin?q=abc123", TimeoutS: 5,
		BodyRandomBytes: 2048,
		Headers:         map[string]string{"User-Agent": "CortexSimBeacon/1.0", "X-Simulation-Run-ID": "cortexsim-t"}}
	sum, err := (&Runner{}).Run(context.Background(), []Op{op}, []string{host})
	if err != nil || sum.Sent != 1 || sum.NotSent != 0 {
		t.Fatalf("run: err=%v sum=%+v", err, sum)
	}
	want := seen{"POST", "/checkin?q=abc123", "CortexSimBeacon/1.0", "cortexsim-t", 2048}
	if s := <-got; s != want {
		t.Errorf("listener saw %+v, want %+v", s, want)
	}
}

func TestTCPOpSendsFramesInOrderAndProbesAClosedPort(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	received := make(chan []byte, 1)
	go func() {
		conn, err := ln.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		_, _ = conn.Write([]byte("220 hello\r\n"))
		data, _ := io.ReadAll(conn)
		received <- data
	}()
	host, portText, _ := net.SplitHostPort(ln.Addr().String())
	port, _ := strconv.Atoi(portText)

	// A port nothing listens on: bind one, then let it go.
	closed, _ := net.Listen("tcp", "127.0.0.1:0")
	_, closedPortText, _ := net.SplitHostPort(closed.Addr().String())
	closedPort, _ := strconv.Atoi(closedPortText)
	closed.Close()

	b64 := base64.StdEncoding.EncodeToString
	ops := []Op{
		{Op: "tcp", Host: host, Port: port, TimeoutS: 2, Frames: []Frame{
			{Read: 1},
			{SendB64: b64([]byte("USER cortexsim\r\n")), Read: 0.05},
			{SendB64: b64([]byte("QUIT\r\n"))},
		}},
		{Op: "tcp", Host: host, Port: closedPort, TimeoutS: 1, Probe: true},
		{Op: "tcp", Host: host, Port: closedPort, TimeoutS: 1},
	}
	allow := []string{ln.Addr().String(), host + ":" + closedPortText}
	sum, err := (&Runner{}).Run(context.Background(), ops, allow)
	if err != nil {
		t.Fatal(err)
	}
	if data := <-received; !bytes.Equal(data, []byte("USER cortexsim\r\nQUIT\r\n")) {
		t.Errorf("listener received %q", data)
	}
	// The session and the probe reached; the plain connect to a closed port failed.
	if sum.Sent != 2 || sum.NotSent != 1 || len(sum.Failures) != 1 {
		t.Errorf("summary: %+v", sum)
	}
}

func TestDNSOpSendsOneQueryToTheNamedResolver(t *testing.T) {
	pc, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer pc.Close()
	op := Op{Op: "dns", QName: "mfrggzdf.exfil-0000.exfil.example.test", QType: "TXT",
		Resolver: pc.LocalAddr().String()}
	done := make(chan Summary, 1)
	go func() {
		sum, _ := (&Runner{}).Run(context.Background(), []Op{op},
			[]string{"dns:exfil.example.test", pc.LocalAddr().String()})
		done <- sum
	}()
	buf := make([]byte, 512)
	_ = pc.SetReadDeadline(time.Now().Add(5 * time.Second))
	n, from, err := pc.ReadFrom(buf)
	if err != nil {
		t.Fatalf("no query arrived: %v", err)
	}
	_, _ = pc.WriteTo(buf[:n], from) // any reply ends the wait
	if name, qtype := parseQuery(t, buf[:n]); name != op.QName || qtype != "TXT" {
		t.Errorf("resolver was asked %s %s", qtype, name)
	}
	if sum := <-done; sum.Sent != 1 {
		t.Errorf("summary: %+v", sum)
	}
}

// resetAfterRequest is a loopback listener that reads each HTTP request in
// full and then resets the connection instead of answering, as the lab's
// server did in #374. It reports each request it read.
func resetAfterRequest(t *testing.T) (addr string, requests <-chan string) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	got := make(chan string, 64)
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				req, err := http.ReadRequest(bufio.NewReader(c))
				if err == nil {
					_, _ = io.Copy(io.Discard, req.Body)
					got <- req.Method + " " + req.URL.RequestURI()
				}
				_ = c.(*net.TCPConn).SetLinger(0) // close with a reset, not a FIN
				_ = c.Close()
			}(conn)
		}
	}()
	return ln.Addr().String(), got
}

func TestHTTPOpResetAfterTheRequestIsWrittenCountsAsSent(t *testing.T) {
	addr, requests := resetAfterRequest(t)
	op := Op{Op: "http", Method: "GET", URL: "http://" + addr + "/uid/index.html?q=abc123", TimeoutS: 5}
	var replyErr error // what the real transport returned
	runner := &Runner{}
	runner.HTTP = func(req *http.Request, op Op) (*http.Response, error) {
		resp, err := runner.realHTTP(req, op)
		replyErr = err
		return resp, err
	}
	sum, err := runner.Run(context.Background(), []Op{op}, []string{addr})
	if err != nil {
		t.Fatal(err)
	}
	if replyErr == nil {
		t.Fatal("the far end answered instead of resetting")
	}
	if got := <-requests; got != "GET /uid/index.html?q=abc123" {
		t.Errorf("the listener read %q", got)
	}
	if sum.Sent != 1 || sum.NotSent != 0 || sum.BytesSent == 0 || sum.Replies["reset"] != 1 {
		t.Errorf("a request the far end read and then reset was not counted as sent: %+v", sum)
	}
	if line := sum.Line(); line != "1 of 1 sent · 1 reset by peer" {
		t.Errorf("line: %q", line)
	}
	// Each platform words a reset its own way (Windows: "forcibly closed by
	// the remote host"), so the error is judged by its value, through the
	// helper that classed it, never by its text.
	if got := replyClass(replyErr); got != replyReset {
		t.Errorf("the socket's reset reads %q: %v", got, replyErr)
	}
	if len(sum.ReplyErrors) != 1 || !strings.HasSuffix(sum.ReplyErrors[0], replyErr.Error()) {
		t.Errorf("the reset is not on record: %v", sum.ReplyErrors)
	}
}

// socketCode is one socket error the platform returns and the reply class it
// is (errno_unix_test.go, errno_windows_test.go).
type socketCode struct {
	name  string
	call  string
	errno syscall.Errno
	reply string
}

// The platform's own codes, wrapped as net/http hands them back, read as
// their reply class.
func TestReplyClassReadsTheSocketErrorByValue(t *testing.T) {
	for _, c := range socketCodes {
		t.Run(c.name, func(t *testing.T) {
			err := &url.Error{Op: "Get", URL: "http://c2.example.test/", Err: &net.OpError{
				Op: "read", Net: "tcp", Err: os.NewSyscallError(c.call, c.errno)}}
			if got := replyClass(err); got != c.reply {
				t.Errorf("%v reads %q, want %q", err, got, c.reply)
			}
		})
	}
}

func TestHTTPOpServerErrorCountsAsSent(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	host := strings.TrimPrefix(srv.URL, "http://")
	sum, err := (&Runner{}).Run(context.Background(),
		[]Op{{Op: "http", Method: "GET", URL: srv.URL + "/x", TimeoutS: 5}}, []string{host})
	if err != nil || sum.Sent != 1 || sum.NotSent != 0 || sum.Replies["http_500"] != 1 {
		t.Errorf("a request answered 500 was not counted as sent: err=%v sum=%+v", err, sum)
	}
	if line := sum.Line(); line != "1 of 1 sent · 1 answered HTTP 500" {
		t.Errorf("line: %q", line)
	}
}

func TestHTTPOpToAClosedPortIsNotSent(t *testing.T) {
	closed, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := closed.Addr().String()
	closed.Close()
	sum, err := (&Runner{}).Run(context.Background(),
		[]Op{{Op: "http", Method: "GET", URL: "http://" + addr + "/x", TimeoutS: 2}}, []string{addr})
	if err != nil || sum.Sent != 0 || sum.NotSent != 1 || sum.BytesSent != 0 || len(sum.Failures) != 1 {
		t.Errorf("a refused connection was counted as sent: err=%v sum=%+v", err, sum)
	}
	if line := sum.Line(); line != "0 of 1 sent · 1 not sent" {
		t.Errorf("line: %q", line)
	}
}

// The same rule through an injected transport, which says through the
// request's trace hooks how far the request got before the error.
func TestHTTPOpIsSentOnceTheWholeRequestIsWritten(t *testing.T) {
	reset := &net.OpError{Op: "read", Net: "tcp", Err: os.NewSyscallError("read", syscall.ECONNRESET)}
	cases := []struct {
		name  string
		got   func(trace *httptrace.ClientTrace) error
		sent  bool
		reply string
	}{
		{"written, then reset", func(tr *httptrace.ClientTrace) error {
			tr.GotConn(httptrace.GotConnInfo{})
			tr.WroteRequest(httptrace.WroteRequestInfo{})
			return reset
		}, true, "reset"},
		{"written, then no reply in time", func(tr *httptrace.ClientTrace) error {
			tr.GotConn(httptrace.GotConnInfo{})
			tr.WroteRequest(httptrace.WroteRequestInfo{})
			return context.DeadlineExceeded
		}, true, "timeout_waiting_reply"},
		{"written, then closed without a reply", func(tr *httptrace.ClientTrace) error {
			tr.GotConn(httptrace.GotConnInfo{})
			tr.WroteRequest(httptrace.WroteRequestInfo{})
			return io.EOF
		}, true, "closed"},
		{"connected, the write failed", func(tr *httptrace.ClientTrace) error {
			tr.GotConn(httptrace.GotConnInfo{})
			tr.WroteRequest(httptrace.WroteRequestInfo{Err: reset})
			return reset
		}, false, ""},
		{"connected, nothing written", func(tr *httptrace.ClientTrace) error {
			tr.GotConn(httptrace.GotConnInfo{})
			return reset
		}, false, ""},
		{"the name did not resolve", func(*httptrace.ClientTrace) error {
			return &net.DNSError{Err: "no such host", Name: "c2.example.test", IsNotFound: true}
		}, false, ""},
	}
	op := Op{Op: "http", Method: "GET", URL: "http://c2.example.test:8080/checkin"}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			runner := &Runner{HTTP: func(req *http.Request, _ Op) (*http.Response, error) {
				return nil, tc.got(httptrace.ContextClientTrace(req.Context()))
			}}
			sum, err := runner.Run(context.Background(), []Op{op}, []string{"c2.example.test:8080"})
			if err != nil {
				t.Fatal(err)
			}
			if got := sum.Sent == 1; got != tc.sent {
				t.Fatalf("sent=%v, want %v: %+v", got, tc.sent, sum)
			}
			if tc.sent && (sum.Replies[tc.reply] != 1 || sum.BytesSent == 0) {
				t.Errorf("reply class or bytes: %+v", sum)
			}
			if !tc.sent && (sum.NotSent != 1 || sum.BytesSent != 0 || len(sum.Failures) != 1) {
				t.Errorf("not counted as not sent: %+v", sum)
			}
		})
	}
}
