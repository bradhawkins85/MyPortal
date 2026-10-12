package checks

import (
	"context"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

// CheckService reports a service's state, first starting, restarting or
// stopping it when it is not in the expected state and the check asks for that.
func CheckService(ctx context.Context, want client.CheckService) client.ServiceStatus {
	result := client.ServiceStatus{Name: want.Name}
	if !validName(want.Name) {
		result.Status, result.Error = "unknown", "not a service name"
		return result
	}
	status, err := serviceStatus(ctx, want.Name)
	result.Status = status
	if err != nil {
		result.Error = err.Error()
		return result
	}
	if status == "missing" || status == "unsupported" || status == want.Expected {
		return result
	}
	var verb, done string
	var fix func(context.Context, string) error
	switch {
	case want.Expected == "running" && want.Fix == "start":
		verb, done, fix = "start", "started", startService
	case want.Expected == "running" && want.Fix == "restart":
		verb, done, fix = "restart", "restarted", restartService
	case want.Expected == "stopped" && want.Fix == "stop":
		verb, done, fix = "stop", "stopped", stopService
	default:
		return result
	}
	if err := fix(ctx, want.Name); err != nil {
		result.Action, result.Error = "could not "+verb+" it", err.Error()
	} else {
		result.Action = done + " it"
	}
	if after, err := serviceStatus(ctx, want.Name); err == nil {
		result.Status = after
	}
	return result
}
