//go:build !windows

package secdir

import (
	"fmt"
	"os"
)

// Secure creates path if needed and gives it owner-only (or world-readable
// for UsersRead) permissions. It refuses a symbolic link in place of the
// folder.
func Secure(path string, access Access) error {
	mode := os.FileMode(0o700)
	if access == UsersRead {
		mode = 0o755
	}
	if err := os.MkdirAll(path, mode); err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return fmt.Errorf("%s is not a folder", path)
	}
	return os.Chmod(path, mode)
}

// SecureFile gives an existing file owner-only permissions, or world-readable
// ones for UsersRead.
func SecureFile(path string, access Access) error {
	mode := os.FileMode(0o600)
	if access == UsersRead {
		mode = 0o644
	}
	return os.Chmod(path, mode)
}

// Sweep is not needed outside Windows: these folders sit under root-owned
// locations that other users cannot create entries in.
func Sweep(path string) []string { return nil }
