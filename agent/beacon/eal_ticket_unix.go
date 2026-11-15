//go:build !windows

package beacon

import (
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"strconv"
	"strings"
)

// sharedTicketBase is where a ticket directory goes when a step runs as
// another account: that account has to be able to walk to it, and a root
// beacon's own temp directory can be closed to everyone else (macOS). /tmp is
// world-searchable and sticky.
const sharedTicketBase = "/tmp"

// ealChown hands a ticket file to the step's account. A var so a test that is
// not root can see who the file would have gone to.
var ealChown = func(f *os.File, uid, gid int) error { return f.Chown(uid, gid) }

// newTicketDir makes the directory this task's ticket files live in. Its name
// is random and it is created, not adopted (os.MkdirTemp fails over to a new
// name if one exists), so nothing can be planted at the path first. Nobody but
// the beacon can list or write it. When shared, other accounts may pass
// through it (0711) to a file they are told the name of; otherwise not even
// that (0700).
func newTicketDir(shared bool) (string, error) {
	base, mode := os.TempDir(), os.FileMode(0o700)
	if shared {
		mode = 0o711
		if info, err := os.Stat(sharedTicketBase); err == nil && info.IsDir() {
			base = sharedTicketBase
		}
	}
	dir, err := os.MkdirTemp(base, "cortexsim-eal-")
	if err != nil {
		return "", err
	}
	if err := os.Chmod(dir, mode); err != nil {
		_ = os.Remove(dir)
		return "", err
	}
	return dir, nil
}

// writeTicketFile creates the step's ticket file: new (never an existing
// path), mode 0600, owned by the account the step runs as. The ticket is
// written only once the file belongs to that account.
func writeTicketFile(path, ticket string, owner *ticketOwner) error {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	if owner != nil {
		err = ealChown(f, owner.uid, owner.gid)
	}
	if err == nil {
		_, err = f.WriteString(ticket + "\n")
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		_ = os.Remove(path)
	}
	return err
}

// lookupAccountIDs is an account's uid and gid. The passwd file is tried
// first; `id` covers an account that only a directory service knows.
func lookupAccountIDs(name string) (uid, gid int, err error) {
	if u, lerr := user.Lookup(name); lerr == nil {
		uid, err = strconv.Atoi(u.Uid)
		if err == nil {
			gid, err = strconv.Atoi(u.Gid)
		}
		if err == nil {
			return uid, gid, nil
		}
	}
	for i, flag := range []string{"-u", "-g"} {
		out, ierr := exec.Command("id", flag, "--", name).Output()
		if ierr != nil {
			return 0, 0, fmt.Errorf("account %q: %w", name, ierr)
		}
		n, perr := strconv.Atoi(strings.TrimSpace(string(out)))
		if perr != nil {
			return 0, 0, fmt.Errorf("account %q: %w", name, perr)
		}
		if i == 0 {
			uid = n
		} else {
			gid = n
		}
	}
	return uid, gid, nil
}
