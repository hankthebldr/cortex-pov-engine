//go:build windows

package identity

// Token elevation on Windows (CONT-14). The question is not "is this account
// an administrator" — under UAC an administrator's process normally runs with
// a FILTERED token that has no administrative rights — but "does THIS process's
// token carry them", which is exactly what TOKEN_ELEVATION reports.

import (
	"syscall"
	"unsafe"
)

// tokenElevationClass is TOKEN_INFORMATION_CLASS TokenElevation.
const tokenElevationClass = 20

func tokenElevated() bool {
	tok, err := syscall.OpenCurrentProcessToken()
	if err != nil {
		return false // unknown is reported as NOT elevated: never claim rights we cannot see
	}
	defer tok.Close()

	var elevation uint32 // TOKEN_ELEVATION { DWORD TokenIsElevated; }
	var n uint32
	if err := syscall.GetTokenInformation(tok, tokenElevationClass,
		(*byte)(unsafe.Pointer(&elevation)), uint32(unsafe.Sizeof(elevation)), &n); err != nil {
		return false
	}
	return elevation != 0
}
