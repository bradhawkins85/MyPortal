// Package defender collects local Microsoft Defender protection status.
package defender

import (
	"errors"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// ErrUnsupported indicates that Defender status is unavailable on this OS.
var ErrUnsupported = errors.New("Microsoft Defender status is only supported on Windows")

// Collect returns the current Microsoft Defender status.
func Collect() (api.DefenderStatus, error) {
	return collect()
}
