//go:build darwin

package overseer

import (
	"context"
	"fmt"
	"os"
	"strings"
)

type launchdController struct {
	run    commandRunner
	exists func(path string) bool
}

// NewController returns the launchd controller.
func NewController() Controller {
	return launchdController{run: runCommand, exists: func(path string) bool {
		_, err := os.Stat(path)
		return err == nil
	}}
}

func (c launchdController) Status(ctx context.Context, s Service) (State, error) {
	if !c.exists(s.Plist) {
		return State{}, nil
	}
	st := State{Installed: true, Enabled: true}
	if out, err := c.run(ctx, "launchctl", "print-disabled", "system"); err == nil && launchctlDisabled(out, s.Name) {
		st.Enabled = false
	}
	out, err := c.run(ctx, "launchctl", "print", "system/"+s.Name)
	if err != nil {
		// Not loaded: installed but stopped.
		return st, nil
	}
	st.Running = parseLaunchctlState(out)
	return st, nil
}

func (c launchdController) loaded(ctx context.Context, s Service) bool {
	_, err := c.run(ctx, "launchctl", "print", "system/"+s.Name)
	return err == nil
}

func (c launchdController) bootstrap(ctx context.Context, s Service) error {
	if c.loaded(ctx, s) {
		return nil
	}
	if out, err := c.run(ctx, "launchctl", "bootstrap", "system", s.Plist); err != nil && !c.loaded(ctx, s) {
		return fmt.Errorf("launchctl bootstrap %s: %w: %s", s.Plist, err, strings.TrimSpace(string(out)))
	}
	return nil
}

func (c launchdController) Start(ctx context.Context, s Service) error {
	if err := c.bootstrap(ctx, s); err != nil {
		return err
	}
	if out, err := c.run(ctx, "launchctl", "kickstart", "system/"+s.Name); err != nil {
		return fmt.Errorf("launchctl kickstart %s: %w: %s", s.Name, err, strings.TrimSpace(string(out)))
	}
	return nil
}

// Restart kills the running job (kickstart -k) and starts it again.
func (c launchdController) Restart(ctx context.Context, s Service) error {
	if err := c.bootstrap(ctx, s); err != nil {
		return err
	}
	if out, err := c.run(ctx, "launchctl", "kickstart", "-k", "system/"+s.Name); err != nil {
		return fmt.Errorf("launchctl kickstart -k %s: %w: %s", s.Name, err, strings.TrimSpace(string(out)))
	}
	return nil
}
