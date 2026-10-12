//go:build !windows

package checks

import "os/exec"

func hideWindow(*exec.Cmd) {}
