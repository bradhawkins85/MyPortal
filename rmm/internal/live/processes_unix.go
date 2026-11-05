//go:build linux || darwin

package live

import (
	"os/user"
	"sync"
	"syscall"
)

func protectedSystemPID(pid int) bool { return pid == 1 }

func killProcess(pid int, force bool) error {
	signal := syscall.SIGTERM
	if force {
		signal = syscall.SIGKILL
	}
	return syscall.Kill(pid, signal)
}

var userNames sync.Map

func userName(uid string) string {
	if cached, ok := userNames.Load(uid); ok {
		return cached.(string)
	}
	name := uid
	if found, err := user.LookupId(uid); err == nil {
		name = found.Username
	}
	userNames.Store(uid, name)
	return name
}
