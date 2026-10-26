//go:build windows

package main

import (
	"errors"
	"fmt"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
	"golang.org/x/sys/windows/svc/mgr"
)

// configureServiceResilience makes Windows restart the service whenever it
// fails, including a non-zero exit, and start it in Safe Mode. It is safe to
// run repeatedly, so it also repairs services installed by older versions.
func configureServiceResilience(name string) error {
	m, err := mgr.Connect()
	if err != nil {
		return fmt.Errorf("connect to service manager: %w", err)
	}
	defer m.Disconnect()
	s, err := m.OpenService(name)
	if err != nil {
		return fmt.Errorf("open service %s: %w", name, err)
	}
	defer s.Close()

	actions := make([]mgr.RecoveryAction, 0, len(serviceRestartDelays))
	for _, delay := range serviceRestartDelays {
		actions = append(actions, mgr.RecoveryAction{Type: mgr.ServiceRestart, Delay: delay})
	}
	if err := s.SetRecoveryActions(actions, serviceFailureResetPeriod); err != nil {
		return fmt.Errorf("set recovery actions: %w", err)
	}
	if err := s.SetRecoveryActionsOnNonCrashFailures(true); err != nil {
		return fmt.Errorf("set recovery on non-crash failures: %w", err)
	}

	for _, path := range safeBootKeys(name) {
		k, _, err := registry.CreateKey(registry.LOCAL_MACHINE, path, registry.SET_VALUE)
		if err != nil {
			return fmt.Errorf("create %s: %w", path, err)
		}
		err = k.SetStringValue("", "Service")
		k.Close()
		if err != nil {
			return fmt.Errorf("write %s: %w", path, err)
		}
	}
	return nil
}

// removeSafeBootRegistration deletes the Safe Mode keys on uninstall.
func removeSafeBootRegistration(name string) error {
	for _, path := range safeBootKeys(name) {
		if err := registry.DeleteKey(registry.LOCAL_MACHINE, path); err != nil && !errors.Is(err, windows.ERROR_FILE_NOT_FOUND) {
			return fmt.Errorf("delete %s: %w", path, err)
		}
	}
	return nil
}
