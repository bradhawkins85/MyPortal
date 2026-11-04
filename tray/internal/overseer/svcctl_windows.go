//go:build windows

package overseer

import (
	"context"
	"errors"
	"fmt"
	"time"

	"golang.org/x/sys/windows"
	winsvc "golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

type scmController struct{}

// NewController returns the Windows service control manager controller.
func NewController() Controller { return scmController{} }

func openService(name string) (*mgr.Mgr, *mgr.Service, error) {
	m, err := mgr.Connect()
	if err != nil {
		return nil, nil, fmt.Errorf("connect to service manager: %w", err)
	}
	s, err := m.OpenService(name)
	if err != nil {
		m.Disconnect()
		return nil, nil, err
	}
	return m, s, nil
}

func (scmController) Status(ctx context.Context, s Service) (State, error) {
	m, svc, err := openService(s.Name)
	if errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST) {
		return State{}, nil
	}
	if err != nil {
		return State{}, err
	}
	defer m.Disconnect()
	defer svc.Close()
	st := State{Installed: true, Enabled: true}
	if cfg, err := svc.Config(); err == nil && cfg.StartType == mgr.StartDisabled {
		st.Enabled = false
	}
	status, err := svc.Query()
	if err != nil {
		return State{}, fmt.Errorf("query %s: %w", s.Name, err)
	}
	st.Running = status.State != winsvc.Stopped
	return st, nil
}

func (scmController) Start(ctx context.Context, s Service) error {
	m, svc, err := openService(s.Name)
	if err != nil {
		return err
	}
	defer m.Disconnect()
	defer svc.Close()
	if err := svc.Start(); err != nil && !errors.Is(err, windows.ERROR_SERVICE_ALREADY_RUNNING) {
		return fmt.Errorf("start %s: %w", s.Name, err)
	}
	return nil
}

// Restart stops the service and starts it again. A service that does not stop
// within 45 seconds is wedged, so its process is ended.
func (scmController) Restart(ctx context.Context, s Service) error {
	m, svc, err := openService(s.Name)
	if err != nil {
		return err
	}
	defer m.Disconnect()
	defer svc.Close()
	status, err := svc.Query()
	if err != nil {
		return fmt.Errorf("query %s: %w", s.Name, err)
	}
	if status.State != winsvc.Stopped {
		// A wedged service may refuse the stop request; ending it below still works.
		_, _ = svc.Control(winsvc.Stop)
		if !waitStopped(ctx, svc, 45*time.Second) {
			if err := terminate(status.ProcessId); err != nil {
				return fmt.Errorf("end %s process %d: %w", s.Name, status.ProcessId, err)
			}
			if !waitStopped(ctx, svc, 15*time.Second) {
				return fmt.Errorf("%s did not stop", s.Name)
			}
		}
	}
	if err := svc.Start(); err != nil && !errors.Is(err, windows.ERROR_SERVICE_ALREADY_RUNNING) {
		return fmt.Errorf("start %s: %w", s.Name, err)
	}
	return nil
}

func waitStopped(ctx context.Context, svc *mgr.Service, limit time.Duration) bool {
	deadline := time.Now().Add(limit)
	for {
		status, err := svc.Query()
		if err == nil && status.State == winsvc.Stopped {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(500 * time.Millisecond):
		}
	}
}

func terminate(pid uint32) error {
	if pid == 0 {
		return errors.New("no process ID")
	}
	h, err := windows.OpenProcess(windows.PROCESS_TERMINATE, false, pid)
	if err != nil {
		return err
	}
	defer windows.CloseHandle(h)
	return windows.TerminateProcess(h, 1)
}
