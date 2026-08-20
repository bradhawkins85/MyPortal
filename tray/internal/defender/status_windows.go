//go:build windows

package defender

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"os/exec"
	"strings"
	"unicode/utf16"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

const statusScript = `$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
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
	cmd := exec.Command(powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return api.DefenderStatus{}, fmt.Errorf("query Microsoft Defender: %w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return decodeStatus(out)
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
