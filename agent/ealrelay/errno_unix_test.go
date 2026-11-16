//go:build !windows

package ealrelay

import "syscall"

// socketCodes is what the socket layer hands back for each way a connection
// ends.
var socketCodes = []socketCode{
	{"read after a reset", "read", syscall.ECONNRESET, replyReset},
	{"write after a reset", "write", syscall.EPIPE, replyReset},
	{"port closed", "connect", syscall.ECONNREFUSED, replyRefused},
}
