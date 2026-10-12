//go:build !windows

package updater

import (
	"os/exec"
	"syscall"
)

// detach starts the installer in its own process group, so launchd stopping
// the service for the upgrade does not stop it.
func detach(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
}
