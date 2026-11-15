package executor

import "os"

// SameFile reports whether a and b are the same file on disk (same device and
// inode, so a hard link counts). Any stat error means "not the same file".
func SameFile(a, b string) bool {
	ai, err := os.Stat(a)
	if err != nil {
		return false
	}
	bi, err := os.Stat(b)
	if err != nil {
		return false
	}
	return os.SameFile(ai, bi)
}
