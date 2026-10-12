//go:build windows

package checks

import (
	"os/exec"
	"syscall"
)

// hideWindow stops a console window flashing up for each command.
func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
}
