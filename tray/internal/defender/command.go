package defender

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// ErrUnsupportedCommand indicates a command unknown to this tray version.
var ErrUnsupportedCommand = errors.New("unsupported Microsoft Defender command")

// None of these commands change a setting that Tamper Protection guards:
// scans, signature updates and threat remediation are permitted actions, and
// Windows Firewall is outside Tamper Protection's scope.
const enableFirewallScript = `$policyRoot = 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsFirewall'
$forcedOff = @(foreach ($profile in @(@('Domain', 'DomainProfile'), @('Private', 'PrivateProfile'), @('Public', 'PublicProfile'))) {
  try {
    $value = (Get-ItemProperty -LiteralPath (Join-Path $policyRoot $profile[1]) -Name 'EnableFirewall' -ErrorAction Stop).EnableFirewall
    if ($value -eq 0) { $profile[0] }
  } catch {}
})
Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True
$stillOff = @(Get-NetFirewallProfile -PolicyStore ActiveStore | Where-Object { -not $_.Enabled } | ForEach-Object { [string]$_.Name })
if ($stillOff.Count -gt 0) {
  $message = 'Windows Firewall is still off for: ' + ($stillOff -join ', ') + '.'
  if ($forcedOff.Count -gt 0) {
    $message += ' Group Policy or MDM turns the firewall off for ' + ($forcedOff -join ', ') + '; change that policy instead.'
  }
  throw $message
}`

// Execute runs an administrator-requested Defender action on the endpoint.
func Execute(ctx context.Context, commandType, detectionUID string) error {
	script, err := commandScript(commandType, detectionUID)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, CommandTimeout(commandType))
	defer cancel()
	if _, err := runPowerShell(ctx, script); err != nil {
		return fmt.Errorf("execute Microsoft Defender command: %w", err)
	}
	return nil
}

// IsScan reports whether a command starts a long-running Defender scan.
func IsScan(commandType string) bool {
	return commandType == "quick_scan" || commandType == "full_scan"
}

// CommandTimeout bounds how long a command may run. Start-MpScan blocks until
// the scan finishes, so a full scan can legitimately take many hours; the
// portal expires unfinished commands after 24 hours.
func CommandTimeout(commandType string) time.Duration {
	switch commandType {
	case "full_scan":
		return 20 * time.Hour
	case "quick_scan":
		return 2 * time.Hour
	default:
		return 30 * time.Minute
	}
}

func commandScript(commandType, detectionUID string) (string, error) {
	switch commandType {
	case "quick_scan":
		return "Start-MpScan -ScanType QuickScan", nil
	case "full_scan":
		return "Start-MpScan -ScanType FullScan", nil
	case "signature_update":
		return "Update-MpSignature", nil
	case "enable_firewall":
		return enableFirewallScript, nil
	case "quarantine", "remediate":
		if detectionUID == "" {
			return "", fmt.Errorf("%s requires a detection identifier", commandType)
		}
		// Remove-MpThreat applies Defender's configured remediation action to
		// active threats. It has no per-threat parameter; the identifier is
		// required here to prevent an unscoped action from a malformed command.
		return "Remove-MpThreat", nil
	default:
		return "", fmt.Errorf("%w: %s", ErrUnsupportedCommand, commandType)
	}
}
