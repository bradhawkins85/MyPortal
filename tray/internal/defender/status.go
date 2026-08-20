// Package defender collects local Microsoft Defender protection status.
package defender

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"

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
	return status, nil
}
