//go:build windows

package ealrelay

import "syscall"

// socketCodes is what Winsock hands back for each way a connection ends.
var socketCodes = []socketCode{
	{"read after a reset", "wsarecv", syscall.WSAECONNRESET, replyReset},
	{"send after a reset", "wsasend", syscall.WSAECONNABORTED, replyReset},
	{"port closed", "connectex", wsaeconnrefused, replyRefused},
}
