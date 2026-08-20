package defender

import (
	"errors"
	"fmt"
)

// ErrUnsupportedCommand indicates a command unknown to this tray version.
var ErrUnsupportedCommand = errors.New("unsupported Microsoft Defender command")

// Execute runs an administrator-requested Defender action on the endpoint.
func Execute(commandType, detectionUID string) error {
	script, err := commandScript(commandType, detectionUID)
	if err != nil {
		return err
	}
	return executePowerShell(script)
}

func commandScript(commandType, detectionUID string) (string, error) {
	switch commandType {
	case "quick_scan":
		return "Start-MpScan -ScanType QuickScan", nil
	case "full_scan":
		return "Start-MpScan -ScanType FullScan", nil
	case "signature_update":
		return "Update-MpSignature", nil
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
