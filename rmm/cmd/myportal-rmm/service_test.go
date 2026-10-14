package main

import (
	"errors"
	"fmt"
	"strings"
	"testing"

	"github.com/kardianos/service"
)

func TestUninstallServiceIsIdempotent(t *testing.T) {
	for _, missing := range []error{
		service.ErrNotInstalled,
		fmt.Errorf("query: %w", service.ErrNotInstalled),
	} {
		for _, phase := range []string{"status", "uninstall"} {
			t.Run(phase+"/"+missing.Error(), func(t *testing.T) {
				var actions []string
				err := uninstallService(func() (service.Status, error) {
					if phase == "status" {
						return service.StatusUnknown, missing
					}
					return service.StatusStopped, nil
				}, func(action string) error {
					actions = append(actions, action)
					if action == "uninstall" {
						return missing
					}
					return nil
				})
				if err != nil {
					t.Fatal(err)
				}
				want := "stop,uninstall"
				if phase == "status" {
					want = ""
				}
				if strings.Join(actions, ",") != want {
					t.Fatalf("actions=%v", actions)
				}
			})
		}
	}
}

func TestUninstallNamedMissingErrorRequiresConfirmedAbsence(t *testing.T) {
	missing := errors.New("Failed to uninstall MyPortal RMM Agent: service MyPortalRMMAgent is not installed")
	for _, absent := range []bool{true, false} {
		queries := 0
		err := uninstallService(func() (service.Status, error) {
			queries++
			if queries > 1 && absent {
				return service.StatusUnknown, service.ErrNotInstalled
			}
			return service.StatusStopped, nil
		}, func(action string) error {
			if action == "uninstall" {
				return missing
			}
			return nil
		})
		if (err == nil) != absent || queries != 2 {
			t.Fatalf("absent=%v err=%v queries=%d", absent, err, queries)
		}
	}
}

func TestUninstallServicePreservesRealErrors(t *testing.T) {
	for _, phase := range []string{"status", "uninstall"} {
		t.Run(phase, func(t *testing.T) {
			denied := errors.New("access denied")
			err := uninstallService(func() (service.Status, error) {
				if phase == "status" {
					return service.StatusUnknown, denied
				}
				return service.StatusStopped, nil
			}, func(action string) error {
				if phase == "status" {
					t.Fatalf("control called after query failed: %s", action)
				}
				return denied
			})
			if !errors.Is(err, denied) {
				t.Fatalf("err=%v", err)
			}
		})
	}
}
