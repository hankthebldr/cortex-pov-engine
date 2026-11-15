//go:build windows

package beacon

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"
)

// On Windows a step always runs as the beacon's own account (there is no
// impersonation), so the ticket directory and file are created for that
// account alone. The access list is part of the create call itself: there is
// no moment at which the object exists with an inherited, wider one.

var (
	ealAdvapi32             = syscall.NewLazyDLL("advapi32.dll")
	procConvertStringSDToSD = ealAdvapi32.NewProc("ConvertStringSecurityDescriptorToSecurityDescriptorW")
)

// sddlRevision1 is SDDL_REVISION_1.
const sddlRevision1 = 1

// ownerOnlyAttributes returns security attributes whose access list is
// protected (nothing inherited) and grants full access to this process's
// account and to nobody else, and a func that frees them.
func ownerOnlyAttributes(inheritable bool) (*syscall.SecurityAttributes, func(), error) {
	tok, err := syscall.OpenCurrentProcessToken()
	if err != nil {
		return nil, nil, err
	}
	defer tok.Close()
	tu, err := tok.GetTokenUser()
	if err != nil {
		return nil, nil, err
	}
	sid, err := tu.User.Sid.String()
	if err != nil {
		return nil, nil, err
	}
	flags := ""
	if inheritable {
		flags = "OICI" // files made inside the directory get the same list
	}
	sddl, err := syscall.UTF16PtrFromString("D:P(A;" + flags + ";FA;;;" + sid + ")")
	if err != nil {
		return nil, nil, err
	}
	var sd uintptr
	ok, _, callErr := procConvertStringSDToSD.Call(
		uintptr(unsafe.Pointer(sddl)), sddlRevision1, uintptr(unsafe.Pointer(&sd)), 0)
	if ok == 0 {
		return nil, nil, fmt.Errorf("build the access list: %w", callErr)
	}
	sa := &syscall.SecurityAttributes{SecurityDescriptor: sd}
	sa.Length = uint32(unsafe.Sizeof(*sa))
	return sa, func() { _, _ = syscall.LocalFree(syscall.Handle(sd)) }, nil
}

// newTicketDir makes the directory this task's ticket files live in: a random
// name, created (never adopted), with an access list for the beacon's account
// only.
func newTicketDir(_ bool) (string, error) {
	sa, free, err := ownerOnlyAttributes(true)
	if err != nil {
		return "", err
	}
	defer free()
	for attempt := 0; attempt < 8; attempt++ {
		name, err := randomName()
		if err != nil {
			return "", err
		}
		dir := filepath.Join(os.TempDir(), "cortexsim-eal-"+name)
		p, err := syscall.UTF16PtrFromString(dir)
		if err != nil {
			return "", err
		}
		err = syscall.CreateDirectory(p, sa)
		if err == nil {
			return dir, nil
		}
		if !errors.Is(err, syscall.ERROR_ALREADY_EXISTS) {
			return "", err
		}
	}
	return "", errors.New("no free name for the ticket directory")
}

// writeTicketFile creates the step's ticket file: new (never an existing
// path), with an access list for the beacon's account only, set by the create
// call.
func writeTicketFile(path, ticket string, _ *ticketOwner) error {
	sa, free, err := ownerOnlyAttributes(false)
	if err != nil {
		return err
	}
	defer free()
	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	h, err := syscall.CreateFile(p, syscall.GENERIC_WRITE, 0, sa,
		syscall.CREATE_NEW, syscall.FILE_ATTRIBUTE_NORMAL, 0)
	if err != nil {
		return err
	}
	f := os.NewFile(uintptr(h), path)
	_, err = f.WriteString(ticket + "\n")
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		_ = os.Remove(path)
	}
	return err
}

// lookupAccountIDs has no meaning here: a Windows step runs as the beacon.
func lookupAccountIDs(name string) (int, int, error) {
	return 0, 0, fmt.Errorf("account %q: no uid on windows", name)
}
