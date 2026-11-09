//go:build windows

package secdir

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	// SYSTEM and Administrators: full control, inherited by everything inside.
	privateSDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
	// As above, plus read and execute (never write) for local users.
	usersReadSDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"
)

func sddlFor(access Access) string {
	if access == UsersRead {
		return usersReadSDDL
	}
	return privateSDDL
}

// Secure makes sure path is a real folder owned by SYSTEM or Administrators
// with a protected access list for access.
//
// A folder that another user created first (or a link put in its place) is
// moved aside to "<path>.untrusted-<time>" and a fresh folder is created, so
// nothing planted in it is ever read by the service.
func Secure(path string, access Access) error {
	sd, err := windows.SecurityDescriptorFromString(sddlFor(access))
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	trusted, exists, err := trustedFolder(path)
	if err != nil {
		return err
	}
	if exists && !trusted {
		aside := fmt.Sprintf("%s.untrusted-%d", path, time.Now().UnixNano())
		if err := os.Rename(path, aside); err != nil {
			return fmt.Errorf("move untrusted folder %s aside: %w", path, err)
		}
		exists = false
	}
	if !exists {
		return createWith(path, sd)
	}
	if protected, _ := hasProtectedDACL(path); protected {
		// Already set by an earlier start (or deliberately by an administrator).
		return nil
	}
	dacl, _, err := sd.DACL()
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT,
		windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION,
		nil, nil, dacl, nil)
}

// Sweep removes anything inside path that is a link or junction, or is not
// owned by SYSTEM or Administrators. Call it at service start, after Secure,
// before reading anything from the folder. It returns what it removed.
func Sweep(path string) []string {
	var removed []string
	var walk func(dir string)
	walk = func(dir string) {
		entries, err := os.ReadDir(dir)
		if err != nil {
			return
		}
		for _, entry := range entries {
			p := filepath.Join(dir, entry.Name())
			reparse, err := isReparsePoint(p)
			if err != nil {
				continue
			}
			if reparse {
				// Removes the link itself, never what it points to.
				if os.Remove(p) == nil {
					removed = append(removed, p)
				}
				continue
			}
			if ok, err := ownedByTrusted(p); err == nil && !ok {
				if removeTree(p) == nil {
					removed = append(removed, p)
				}
				continue
			}
			if entry.IsDir() {
				walk(p)
			}
		}
	}
	walk(path)
	return removed
}

func createWith(path string, sd *windows.SECURITY_DESCRIPTOR) error {
	sa := &windows.SecurityAttributes{
		Length:             uint32(unsafe.Sizeof(windows.SecurityAttributes{})),
		SecurityDescriptor: sd,
	}
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	return windows.CreateDirectory(name, sa)
}

// trustedFolder reports whether path exists and, if so, whether it is a real
// folder (not a link or junction) owned by SYSTEM or Administrators.
func trustedFolder(path string) (trusted, exists bool, err error) {
	info, err := os.Lstat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return false, false, nil
	}
	if err != nil {
		return false, false, err
	}
	reparse, err := isReparsePoint(path)
	if err != nil {
		return false, true, err
	}
	if reparse || !info.IsDir() {
		return false, true, nil
	}
	ok, err := ownedByTrusted(path)
	if err != nil {
		return false, true, err
	}
	return ok, true, nil
}

func isReparsePoint(path string) (bool, error) {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return false, err
	}
	attrs, err := windows.GetFileAttributes(name)
	if err != nil {
		return false, err
	}
	return attrs&windows.FILE_ATTRIBUTE_REPARSE_POINT != 0, nil
}

func ownedByTrusted(path string) (bool, error) {
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION)
	if err != nil {
		return false, err
	}
	owner, _, err := sd.Owner()
	if err != nil {
		return false, err
	}
	if owner == nil {
		return false, nil
	}
	return owner.IsWellKnown(windows.WinLocalSystemSid) ||
		owner.IsWellKnown(windows.WinBuiltinAdministratorsSid), nil
}

func hasProtectedDACL(path string) (bool, error) {
	sd, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return false, err
	}
	control, _, err := sd.Control()
	if err != nil {
		return false, err
	}
	return control&windows.SE_DACL_PROTECTED != 0, nil
}

// removeTree deletes path without following links or junctions inside it.
func removeTree(path string) error {
	reparse, err := isReparsePoint(path)
	if err != nil {
		return err
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	if !reparse && info.IsDir() {
		entries, err := os.ReadDir(path)
		if err != nil {
			return err
		}
		for _, entry := range entries {
			_ = removeTree(filepath.Join(path, entry.Name()))
		}
	}
	return os.Remove(path)
}
