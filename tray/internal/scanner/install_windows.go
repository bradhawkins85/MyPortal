//go:build windows

package scanner

import (
	"fmt"
	"os/exec"
)

func installNmap() error {
	if winget, err := exec.LookPath("winget"); err == nil {
		if err = exec.Command(winget, "install", "--id", "Insecure.Nmap", "--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements").Run(); err == nil {
			return nil
		}
	}
	if choco, err := exec.LookPath("choco"); err == nil {
		if err = exec.Command(choco, "install", "nmap", "-y", "--no-progress").Run(); err == nil {
			return nil
		}
	}
	return fmt.Errorf("could not silently install Nmap (winget or Chocolatey is required)")
}
