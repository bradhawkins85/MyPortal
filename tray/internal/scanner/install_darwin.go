//go:build darwin

package scanner

import (
	"fmt"
	"os/exec"
)

func installNmap() error {
	brew, err := exec.LookPath("brew")
	if err != nil {
		return fmt.Errorf("Homebrew is required to silently install Nmap: %w", err)
	}
	return exec.Command(brew, "install", "nmap").Run()
}
