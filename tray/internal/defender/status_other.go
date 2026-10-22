//go:build !windows

package defender

import (
	"context"
	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func collectDetections(ctx context.Context) ([]api.DefenderDetection, error) {
	return nil, ErrUnsupported
}

func collect() (api.DefenderStatus, error) {
	return api.DefenderStatus{}, ErrUnsupported
}
