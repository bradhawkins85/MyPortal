//go:build linux

package overseer

import (
	"context"
	"fmt"
	"strings"
)

type systemdController struct{ run commandRunner }

// NewController returns the systemd controller.
func NewController() Controller { return systemdController{run: runCommand} }

func (c systemdController) Status(ctx context.Context, s Service) (State, error) {
	out, err := c.run(ctx, "systemctl", "show", "--property=LoadState,ActiveState,UnitFileState", s.Name)
	if err != nil {
		return State{}, fmt.Errorf("systemctl show %s: %w: %s", s.Name, err, strings.TrimSpace(string(out)))
	}
	return parseSystemctlShow(out), nil
}

func (c systemdController) Start(ctx context.Context, s Service) error {
	return c.systemctl(ctx, "start", s.Name)
}

// Restart stops and starts the unit; systemd kills it if it does not stop.
func (c systemdController) Restart(ctx context.Context, s Service) error {
	return c.systemctl(ctx, "restart", s.Name)
}

func (c systemdController) systemctl(ctx context.Context, verb, unit string) error {
	if out, err := c.run(ctx, "systemctl", verb, unit); err != nil {
		return fmt.Errorf("systemctl %s %s: %w: %s", verb, unit, err, strings.TrimSpace(string(out)))
	}
	return nil
}
