//go:build windows

package rmminstall

import (
	"context"
	"errors"
	"fmt"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc/mgr"
)

func platformServiceNotInstalled(err error) bool {
	return errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST)
}

// DeleteService can succeed while deletion is still pending. Close every query
// handle so this wait does not itself prevent Windows from deleting the service.
func waitServiceRemoved(ctx context.Context) error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to service manager: %w", err)
	}
	defer m.Disconnect()
	return waitServiceRemoval(ctx, func() (bool, error) {
		s, err := m.OpenService(rmmServiceName)
		if platformServiceNotInstalled(err) {
			return false, nil
		}
		if errors.Is(err, windows.ERROR_SERVICE_MARKED_FOR_DELETE) {
			return true, nil
		}
		if err != nil {
			return false, err
		}
		return true, s.Close()
	})
}
