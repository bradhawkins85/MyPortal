//go:build !windows

package runner

import (
	"os/exec"
	"syscall"
)

// prepareCommand runs the script in its own process group so a timeout stops
// everything it started, not just the interpreter.
func prepareCommand(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
}
