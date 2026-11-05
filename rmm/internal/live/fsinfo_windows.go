package live

import (
	"io/fs"
	"syscall"
)

// Drives are separate roots on Windows; folders mounted inside another
// drive are reparse points, which the scan does not follow.
func deviceID(fs.FileInfo) (uint64, bool) { return 0, false }

func isReparsePoint(info fs.FileInfo) bool {
	data, ok := info.Sys().(*syscall.Win32FileAttributeData)
	return ok && data.FileAttributes&syscall.FILE_ATTRIBUTE_REPARSE_POINT != 0
}
