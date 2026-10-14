package rmminstall

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kardianos/service"
)

func TestMatchingVersionRestoresMissingOrStoppedServiceWithoutDownload(t *testing.T) {
	for _, scenario := range []string{"missing", "stopped", "running", "access denied"} {
		t.Run(scenario, func(t *testing.T) {
			api := &fakeAPI{token: "t", packageErr: errors.New("offline")}
			in, calls := newInstaller(t, api, "")
			final := filepath.Join(in.Dir, binaryName())
			if err := os.WriteFile(final, []byte("existing agent"), 0o755); err != nil {
				t.Fatal(err)
			}
			running := scenario == "running"
			in.ServiceStatus = func() (service.Status, error) {
				if running {
					return service.StatusRunning, nil
				}
				if scenario == "missing" {
					return service.StatusUnknown, service.ErrNotInstalled
				}
				if scenario == "access denied" {
					return service.StatusUnknown, errors.New("access denied")
				}
				return service.StatusStopped, nil
			}
			in.Run = func(_ context.Context, _ string, args []string, _ []string) ([]byte, error) {
				*calls = append(*calls, call{args: args})
				if args[0] == "version" {
					return []byte("v1.2.3\n"), nil
				}
				if args[0] != "install" {
					t.Fatalf("unexpected command: %v", args)
				}
				running = true
				return nil, nil
			}
			changed, err := in.EnsureVersion(context.Background(), "1.2.3")
			wantChange := scenario == "missing" || scenario == "stopped"
			if changed != wantChange || (err != nil) != (scenario == "access denied") {
				t.Fatalf("changed=%v err=%v", changed, err)
			}
			wantCalls := 1
			if wantChange {
				wantCalls++
			}
			if api.packageCalls != 0 || len(*calls) != wantCalls {
				t.Fatalf("downloads=%d calls=%v", api.packageCalls, *calls)
			}
		})
	}
}

func TestRepairContinuesWhenOlderAgentReportsServiceAbsent(t *testing.T) {
	body := []byte("replacement")
	in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "")
	if err := os.WriteFile(filepath.Join(in.Dir, binaryName()), []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	run := in.Run
	waits := 0
	running := false
	in.ServiceStatus = func() (service.Status, error) {
		if running {
			return service.StatusRunning, nil
		}
		return service.StatusUnknown, service.ErrNotInstalled
	}
	in.WaitServiceRemoved = func(context.Context) error { waits++; return nil }
	in.Run = func(ctx context.Context, path string, args []string, env []string) ([]byte, error) {
		out, err := run(ctx, path, args, env)
		if args[0] == "uninstall" {
			return []byte("myportal-rmm uninstall: Failed to uninstall MyPortal RMM Agent: service MyPortalRMMAgent is not installed"), errors.New("exit status 1")
		}
		if args[0] == "install" && waits == 0 {
			t.Fatal("installed before waiting for deletion")
		}
		if args[0] == "install" {
			running = true
		}
		return out, err
	}
	if _, err := in.RepairVersion(context.Background(), "1.2.3"); err != nil {
		t.Fatal(err)
	}
	var actions []string
	for _, c := range *calls {
		actions = append(actions, c.args[0])
	}
	if strings.Join(actions, ",") != "uninstall,enrol,install,verify" || waits != 1 {
		t.Fatalf("actions=%v waits=%d", actions, waits)
	}
}

func TestDeletionFailurePreservesOldBinaryAndUsesIndependentRecoveryContext(t *testing.T) {
	in, _ := newInstaller(t, &fakeAPI{body: []byte("new"), sha: digest([]byte("new")), token: "t"}, "")
	final := filepath.Join(in.Dir, binaryName())
	if err := os.WriteFile(final, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	in.WaitServiceRemoved = func(context.Context) error { cancel(); return context.Canceled }
	run := in.Run
	restarted := false
	in.Run = func(ctx context.Context, path string, args []string, env []string) ([]byte, error) {
		if args[0] == "install" {
			if ctx.Err() != nil {
				t.Fatal("rollback reused cancelled update context")
			}
			restarted = true
		}
		return run(ctx, path, args, env)
	}
	_, err := in.InstallVersion(ctx, "1.2.3")
	got, readErr := os.ReadFile(final)
	if !errors.Is(err, context.Canceled) || !restarted || readErr != nil || string(got) != "old" {
		t.Fatalf("err=%v restarted=%v binary=%q readErr=%v", err, restarted, got, readErr)
	}
}

func TestLegacyMissingMessageDoesNotHideAServiceThatStillExists(t *testing.T) {
	for _, statusErr := range []error{nil, errors.New("access denied")} {
		in, _ := newInstaller(t, &fakeAPI{}, "")
		uninstallErr := errors.New("exit status 1")
		in.Run = func(context.Context, string, []string, []string) ([]byte, error) {
			return []byte("service MyPortalRMMAgent is not installed"), uninstallErr
		}
		in.ServiceStatus = func() (service.Status, error) { return service.StatusRunning, statusErr }
		if _, err := in.uninstall(context.Background(), "agent"); !errors.Is(err, uninstallErr) {
			t.Fatalf("statusErr=%v uninstallErr=%v", statusErr, err)
		}
	}
}

func TestUpgradeRollsBackWhenServiceDoesNotRun(t *testing.T) {
	in, _ := newInstaller(t, &fakeAPI{body: []byte("new"), sha: digest([]byte("new")), token: "t"}, "")
	final := filepath.Join(in.Dir, binaryName())
	if err := os.WriteFile(final, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	in.ServiceStatus = func() (service.Status, error) { return service.StatusStopped, nil }
	run := in.Run
	in.Run = func(ctx context.Context, path string, args []string, env []string) ([]byte, error) {
		if args[0] == "install" {
			cancel()
		}
		return run(ctx, path, args, env)
	}
	_, err := in.InstallVersion(ctx, "1.2.3")
	got, readErr := os.ReadFile(final)
	if !errors.Is(err, context.Canceled) || readErr != nil || string(got) != "old" {
		t.Fatalf("err=%v binary=%q readErr=%v", err, got, readErr)
	}
}

func TestWaitServiceRemovalHandlesPendingDeletionAndErrors(t *testing.T) {
	queries := 0
	if err := waitServiceRemoval(context.Background(), func() (bool, error) {
		queries++
		return queries < 2, nil
	}); err != nil || queries != 2 {
		t.Fatalf("err=%v queries=%d", err, queries)
	}
	denied := errors.New("access denied")
	if err := waitServiceRemoval(context.Background(), func() (bool, error) {
		return false, denied
	}); !errors.Is(err, denied) {
		t.Fatalf("err=%v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := waitServiceRemoval(ctx, func() (bool, error) { return true, nil }); !errors.Is(err, context.Canceled) {
		t.Fatalf("err=%v", err)
	}
}
