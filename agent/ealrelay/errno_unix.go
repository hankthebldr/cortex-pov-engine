//go:build !windows

package ealrelay

import "syscall"

// The errors a reset or a refused connection comes back as. A write after the
// far end reset the connection fails with EPIPE.
var (
	resetErrnos   = []syscall.Errno{syscall.ECONNRESET, syscall.EPIPE}
	refusedErrnos = []syscall.Errno{syscall.ECONNREFUSED}
)
