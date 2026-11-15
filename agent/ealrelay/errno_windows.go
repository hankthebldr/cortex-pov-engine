//go:build windows

package ealrelay

import "syscall"

// wsaeconnrefused is WSAECONNREFUSED, which package syscall does not name.
const wsaeconnrefused syscall.Errno = 10061

// The errors a reset or a refused connection comes back as. Winsock reports
// its own codes; syscall's ECONNRESET and ECONNREFUSED are kept for an error
// built in Go.
var (
	resetErrnos   = []syscall.Errno{syscall.WSAECONNRESET, syscall.ECONNRESET}
	refusedErrnos = []syscall.Errno{wsaeconnrefused, syscall.ECONNREFUSED}
)
