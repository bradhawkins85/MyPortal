//go:build windows

package scanner

import (
	_ "embed"
	"fmt"
	"os/exec"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

//go:embed scan_windows.ps1
var scanScript string

func Scan() ([]api.NetworkHost, error) {
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		return nil, fmt.Errorf("Windows PowerShell is required for network scanning: %w", err)
	}
	cmd := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encodePowerShellCommand(scanScript))
	out, err := cmd.Output()
	if err != nil {
		if exit, ok := err.(*exec.ExitError); ok {
			return nil, fmt.Errorf("PowerShell network scan: %w: %s", err, strings.TrimSpace(string(exit.Stderr)))
		}
		return nil, fmt.Errorf("PowerShell network scan: %w", err)
	}
	hosts, err := parseNetworkHostsJSON(out)
	if err != nil {
		return nil, fmt.Errorf("parse PowerShell network scan output: %w", err)
	}
	for i := range hosts {
		hosts[i].MACAddress = normalizeMACAddress(hosts[i].MACAddress)
	}
	return hosts, nil
}
