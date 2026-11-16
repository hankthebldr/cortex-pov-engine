//go:build windows

package ealrelay

import "syscall"

// wsaeconnrefused is WSAECONNREFUSED, which package syscall does not name.
const wsaeconnrefused syscall.Errno = 10061

// The errors a reset or a refused connection comes back as. Winsock reports
// its own codes, and never in the words the other platforms use ("forcibly
// closed by the remote host"), so they are matched by value. A read on a
// connection the far end reset fails with WSAECONNRESET; a send on one fails
// with WSAECONNABORTED, Winsock's EPIPE. Package net treats the two as one
// connection error too. syscall's ECONNRESET and ECONNREFUSED are kept for an
// error built in Go.
var (
	resetErrnos   = []syscall.Errno{syscall.WSAECONNRESET, syscall.WSAECONNABORTED, syscall.ECONNRESET}
	refusedErrnos = []syscall.Errno{wsaeconnrefused, syscall.ECONNREFUSED}
)
