package rmminstall

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/kardianos/service"
)

const rmmServiceName = "MyPortalRMMAgent"

func rmmServiceStatus() (service.Status, error) {
	svc, err := service.New(nil, &service.Config{Name: rmmServiceName})
	if err != nil {
		return service.StatusUnknown, err
	}
	status, err := svc.Status()
	if platformServiceNotInstalled(err) {
		err = service.ErrNotInstalled
	}
	return status, err
}

func missingServiceOutput(out []byte) bool {
	return strings.Contains(string(out), "service "+rmmServiceName+" is not installed")
}

func waitServiceRemoval(ctx context.Context, exists func() (bool, error)) error {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	for {
		present, err := exists()
		if err != nil {
			return fmt.Errorf("query deleted agent service: %w", err)
		}
		if !present {
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("wait for agent service deletion: %w", ctx.Err())
		case <-time.After(250 * time.Millisecond):
		}
	}
}

// Repair the registration using the installed executable without downloading
// or re-enrolling a healthy binary, including while the portal is offline.
func (in *Installer) ensureService(ctx context.Context, path string) (bool, error) {
	if in.ServiceStatus == nil {
		return false, nil
	}
	status, err := in.ServiceStatus()
	if err != nil && !errors.Is(err, service.ErrNotInstalled) {
		return false, fmt.Errorf("query agent service: %w", err)
	}
	if err == nil && status == service.StatusRunning {
		return false, nil
	}
	if in.WaitServiceRemoved != nil && errors.Is(err, service.ErrNotInstalled) {
		if err := in.WaitServiceRemoved(ctx); err != nil {
			return false, err
		}
	}
	if out, err := in.Run(ctx, path, []string{"install"}, nil); err != nil {
		return false, fmt.Errorf("restore the agent service: %w: %s", err, trim(out))
	}
	err = in.waitRunning(ctx)
	return err == nil, err
}

func (in *Installer) waitRunning(ctx context.Context) error {
	if in.ServiceStatus == nil {
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	for {
		status, err := in.ServiceStatus()
		if err != nil {
			return fmt.Errorf("query agent service: %w", err)
		}
		if status == service.StatusRunning {
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("agent service did not start: %w", ctx.Err())
		case <-time.After(250 * time.Millisecond):
		}
	}
}

func (in *Installer) uninstall(ctx context.Context, path string) ([]byte, error) {
	out, err := in.Run(ctx, path, []string{"uninstall"}, nil)
	// Older agents report this specific missing-service condition as a failure.
	// Keep repairs compatible without masking permission or deletion errors.
	if err != nil && !missingServiceOutput(out) {
		return out, err
	}
	if in.WaitServiceRemoved != nil {
		if err := in.WaitServiceRemoved(ctx); err != nil {
			return out, err
		}
	}
	if err != nil && in.ServiceStatus != nil {
		if _, statusErr := in.ServiceStatus(); !errors.Is(statusErr, service.ErrNotInstalled) {
			return out, err
		}
	}
	return out, nil
}

func (in *Installer) restartAgent(path string) error {
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	if out, err := in.Run(ctx, path, []string{"install"}, nil); err != nil {
		return fmt.Errorf("restart old agent: %w: %s", err, trim(out))
	}
	return in.waitRunning(ctx)
}
