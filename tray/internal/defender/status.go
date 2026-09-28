// Package defender collects local Microsoft Defender protection status.
package defender

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"sort"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// ErrUnsupported indicates that Defender status is unavailable on this OS.
var ErrUnsupported = errors.New("Microsoft Defender status is only supported on Windows")

// Collect returns the current Microsoft Defender status.
func Collect() (api.DefenderStatus, error) {
	return collect()
}

func decodeStatus(output []byte) (api.DefenderStatus, error) {
	var status api.DefenderStatus
	output = bytes.TrimSpace(bytes.TrimPrefix(output, []byte{0xef, 0xbb, 0xbf}))
	if err := json.Unmarshal(output, &status); err != nil {
		return api.DefenderStatus{}, fmt.Errorf("decode Microsoft Defender status: %w", err)
	}
	limitStatus(&status)
	return status, nil
}

// The portal rejects a status report that exceeds these limits, so a device
// with a long protection history would otherwise never report at all.
const (
	maxReportedDetections    = 100
	maxReportedInfectedFiles = 100
	maxReportedScans         = 20
)

// limitStatus keeps the most recent detections and scans within the portal's
// limits.
func limitStatus(status *api.DefenderStatus) {
	sort.SliceStable(status.Detections, func(i, j int) bool {
		return status.Detections[i].DetectedAt.After(status.Detections[j].DetectedAt)
	})
	if len(status.Detections) > maxReportedDetections {
		status.Detections = status.Detections[:maxReportedDetections]
	}
	for i := range status.Detections {
		if files := status.Detections[i].InfectedFiles; len(files) > maxReportedInfectedFiles {
			status.Detections[i].InfectedFiles = files[:maxReportedInfectedFiles]
		}
	}
	if len(status.ScanHistory) > maxReportedScans {
		status.ScanHistory = status.ScanHistory[:maxReportedScans]
	}
}
