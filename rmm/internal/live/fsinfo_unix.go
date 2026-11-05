//go:build linux || darwin

package live

import (
	"io/fs"
	"syscall"
)

func deviceID(info fs.FileInfo) (uint64, bool) {
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, false
	}
	return uint64(stat.Dev), true
}

func isReparsePoint(fs.FileInfo) bool { return false }
