//go:build !windows && !darwin

package scanner

import (
	"fmt"
	"os/exec"
)

func installNmap() error {
	if apt, err := exec.LookPath("apt-get"); err == nil {
		return exec.Command(apt, "install", "-y", "nmap").Run()
	}
	return fmt.Errorf("automatic Nmap installation is unsupported on this platform")
}
