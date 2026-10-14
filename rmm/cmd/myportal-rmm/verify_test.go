package main

import (
	"context"
	"errors"
	"github.com/kardianos/service"
	"testing"
)

func TestVerifyAgentRequiresRunningServiceAndSuccessfulCheckin(t *testing.T) {
	for _, scenario := range []string{"running", "stopped", "missing", "unauthorized"} {
		t.Run(scenario, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if scenario == "stopped" {
				cancel()
			}
			checked := false
			err := verifyAgent(ctx, func() (service.Status, error) {
				if scenario == "missing" {
					return service.StatusUnknown, service.ErrNotInstalled
				}
				if scenario == "stopped" {
					return service.StatusStopped, nil
				}
				return service.StatusRunning, nil
			}, func(context.Context) error {
				checked = true
				if scenario == "unauthorized" {
					return errors.New("token rejected")
				}
				return nil
			})
			if (err == nil) != (scenario == "running") {
				t.Fatalf("err=%v", err)
			}
			if checked != (scenario == "running" || scenario == "unauthorized") {
				t.Fatalf("checkin=%v", checked)
			}
		})
	}
}
