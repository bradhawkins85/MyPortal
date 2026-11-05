package live

import (
	"context"
	"errors"
	"fmt"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"
)

var serviceStartTypes = []StartType{
	{Value: "auto", Label: "Automatic"},
	{Value: "auto_delayed", Label: "Automatic (delayed start)"},
	{Value: "manual", Label: "Manual"},
	{Value: "disabled", Label: "Disabled"},
}

func stateName(state svc.State) string {
	switch state {
	case svc.Running:
		return "running"
	case svc.Stopped:
		return "stopped"
	case svc.StartPending, svc.ContinuePending:
		return "starting"
	case svc.StopPending:
		return "stopping"
	case svc.Paused, svc.PausePending:
		return "paused"
	}
	return "unknown"
}

func startTypeName(cfg mgr.Config) string {
	switch cfg.StartType {
	case mgr.StartAutomatic:
		if cfg.DelayedAutoStart {
			return "auto_delayed"
		}
		return "auto"
	case mgr.StartManual:
		return "manual"
	case mgr.StartDisabled:
		return "disabled"
	case windows.SERVICE_BOOT_START, windows.SERVICE_SYSTEM_START:
		return "boot"
	}
	return "unknown"
}

func listServices(ctx context.Context) ([]Service, error) {
	m, err := mgr.Connect()
	if err != nil {
		return nil, err
	}
	defer m.Disconnect()
	names, err := m.ListServices()
	if err != nil {
		return nil, err
	}
	out := make([]Service, 0, len(names))
	for _, name := range names {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		s, err := m.OpenService(name)
		if err != nil {
			continue
		}
		item := Service{Name: name, DisplayName: name, Status: "unknown"}
		if status, err := s.Query(); err == nil {
			item.Status = stateName(status.State)
			item.PID = int(status.ProcessId)
		}
		if cfg, err := s.Config(); err == nil {
			if cfg.DisplayName != "" {
				item.DisplayName = cfg.DisplayName
			}
			item.StartType = startTypeName(cfg)
			item.Description = cfg.Description
			item.Account = cfg.ServiceStartName
			// Drivers are listed by sc.exe but are not services to manage here.
			if cfg.ServiceType&(windows.SERVICE_KERNEL_DRIVER|windows.SERVICE_FILE_SYSTEM_DRIVER) != 0 {
				s.Close()
				continue
			}
		}
		s.Close()
		out = append(out, item)
	}
	return out, nil
}

func waitForState(ctx context.Context, s *mgr.Service, want svc.State) error {
	deadline := time.Now().Add(45 * time.Second)
	for {
		status, err := s.Query()
		if err != nil {
			return err
		}
		if status.State == want {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("the service did not become %s in time", stateName(want))
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(300 * time.Millisecond):
		}
	}
}

func controlService(ctx context.Context, name, action string) error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(name)
	if err != nil {
		return fmt.Errorf("open service %s: %w", name, err)
	}
	defer s.Close()
	status, err := s.Query()
	if err != nil {
		return err
	}
	if action == "stop" || action == "restart" {
		if status.State != svc.Stopped {
			if _, err := s.Control(svc.Stop); err != nil && !errors.Is(err, windows.ERROR_SERVICE_NOT_ACTIVE) {
				return fmt.Errorf("stop %s: %w", name, err)
			}
			if err := waitForState(ctx, s, svc.Stopped); err != nil {
				return err
			}
		}
		if action == "stop" {
			return nil
		}
	}
	if err := s.Start(); err != nil && !errors.Is(err, windows.ERROR_SERVICE_ALREADY_RUNNING) {
		return fmt.Errorf("start %s: %w", name, err)
	}
	return waitForState(ctx, s, svc.Running)
}

// setServiceStart changes only the start type, leaving every other
// setting (including indirect description strings) untouched.
func setServiceStart(_ context.Context, name, startType string) error {
	m, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer m.Disconnect()
	s, err := m.OpenService(name)
	if err != nil {
		return fmt.Errorf("open service %s: %w", name, err)
	}
	defer s.Close()
	value := uint32(mgr.StartManual)
	delayed := uint32(0)
	switch startType {
	case "auto":
		value = mgr.StartAutomatic
	case "auto_delayed":
		value, delayed = mgr.StartAutomatic, 1
	case "disabled":
		value = mgr.StartDisabled
	}
	if err := windows.ChangeServiceConfig(s.Handle, windows.SERVICE_NO_CHANGE, value, windows.SERVICE_NO_CHANGE,
		nil, nil, nil, nil, nil, nil, nil); err != nil {
		return fmt.Errorf("change start type of %s: %w", name, err)
	}
	if value == mgr.StartAutomatic {
		info := windows.SERVICE_DELAYED_AUTO_START_INFO{IsDelayedAutoStartUp: delayed}
		if err := windows.ChangeServiceConfig2(s.Handle, windows.SERVICE_CONFIG_DELAYED_AUTO_START_INFO,
			(*byte)(unsafe.Pointer(&info))); err != nil {
			return fmt.Errorf("change delayed start of %s: %w", name, err)
		}
	}
	return nil
}
