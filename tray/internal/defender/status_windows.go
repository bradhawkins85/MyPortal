//go:build windows

package defender

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"unicode/utf16"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

const statusScript = `$ErrorActionPreference = 'Stop'
$s = Get-MpComputerStatus
$health = if (-not $s.AntivirusEnabled -or -not $s.RealTimeProtectionEnabled) { 'critical' } elseif ($s.AntivirusSignatureAge -gt 7) { 'warning' } else { 'healthy' }
$lastScan = @($s.QuickScanEndTime, $s.FullScanEndTime) | Where-Object { $_ } | Sort-Object -Descending | Select-Object -First 1
[ordered]@{
  antivirus_enabled = [bool]$s.AntivirusEnabled
  realtime_protection_enabled = [bool]$s.RealTimeProtectionEnabled
  tamper_protection_enabled = [bool]$s.IsTamperProtected
  signatures_updated_at = if ($s.AntivirusSignatureLastUpdated) { $s.AntivirusSignatureLastUpdated.ToUniversalTime().ToString('o') } else { $null }
  last_scan_at = if ($lastScan) { $lastScan.ToUniversalTime().ToString('o') } else { $null }
  health_status = $health
  details = [ordered]@{ engine_version = $s.AMEngineVersion; product_version = $s.AMProductVersion; signature_version = $s.AntivirusSignatureVersion; signature_age_days = $s.AntivirusSignatureAge }
} | ConvertTo-Json -Depth 4 -Compress`

func collect() (api.DefenderStatus, error) {
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		return api.DefenderStatus{}, fmt.Errorf("locate PowerShell: %w", err)
	}
	encoded := encodePowerShell(statusScript)
	out, err := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded).CombinedOutput()
	if err != nil {
		return api.DefenderStatus{}, fmt.Errorf("query Microsoft Defender: %w: %s", err, strings.TrimSpace(string(out)))
	}
	var status api.DefenderStatus
	if err := json.Unmarshal(out, &status); err != nil {
		return api.DefenderStatus{}, fmt.Errorf("decode Microsoft Defender status: %w", err)
	}
	return status, nil
}

func encodePowerShell(script string) string {
	encoded := utf16.Encode([]rune(script))
	buf := make([]byte, len(encoded)*2)
	for i, value := range encoded {
		buf[i*2] = byte(value)
		buf[i*2+1] = byte(value >> 8)
	}
	return base64.StdEncoding.EncodeToString(buf)
}
