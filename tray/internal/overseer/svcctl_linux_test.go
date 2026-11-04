//go:build linux

package overseer

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestSystemdController(t *testing.T) {
	var calls []string
	c := systemdController{run: func(_ context.Context, name string, args ...string) ([]byte, error) {
		calls = append(calls, name+" "+strings.Join(args, " "))
		if args[0] == "show" {
			return []byte("LoadState=loaded\nActiveState=inactive\nUnitFileState=enabled\n"), nil
		}
		if args[0] == "start" {
			return []byte("Job failed"), errors.New("exit status 1")
		}
		return nil, nil
	}}
	s := Service{Key: ServiceRMM, Name: "MyPortalRMMAgent.service"}
	st, err := c.Status(context.Background(), s)
	if err != nil || st != (State{Installed: true, Enabled: true}) {
		t.Fatalf("Status = %+v, %v", st, err)
	}
	if err := c.Restart(context.Background(), s); err != nil {
		t.Fatal(err)
	}
	if err := c.Start(context.Background(), s); err == nil || !strings.Contains(err.Error(), "Job failed") {
		t.Fatalf("Start error = %v", err)
	}
	want := []string{
		"systemctl show --property=LoadState,ActiveState,UnitFileState MyPortalRMMAgent.service",
		"systemctl restart MyPortalRMMAgent.service",
		"systemctl start MyPortalRMMAgent.service",
	}
	if strings.Join(calls, "\n") != strings.Join(want, "\n") {
		t.Fatalf("calls = %q", calls)
	}
}
