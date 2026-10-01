//go:build !windows

package defender

import "github.com/bradhawkins85/myportal-tray/internal/api"

func collect() (api.DefenderStatus, error) {
	return api.DefenderStatus{}, ErrUnsupported
}
