//go:build windows

package defender

import (
	"bytes"
	"fmt"
	"os/exec"
	"strings"
)

func executePowerShell(script string) error {
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		return fmt.Errorf("locate PowerShell: %w", err)
	}
	cmd := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encodePowerShell("$ErrorActionPreference = 'Stop'\n"+script))
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("execute Microsoft Defender command: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return nil
}
