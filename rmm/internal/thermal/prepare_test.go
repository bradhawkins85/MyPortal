package thermal

import (
	"context"
	"errors"
	"testing"
	"time"
)

func TestPawnIOHardwareEligibility(t *testing.T) {
	for _, tc := range []struct {
		manufacturer, model string
		virtual             bool
	}{
		{"Microsoft Corporation", "Virtual Machine", true},
		{"VMware, Inc.", "VMware Virtual Platform", true},
		{"innotek GmbH", "VirtualBox", true},
		{"QEMU", "Standard PC (Q35 + ICH9, 2009)", true},
		{"Red Hat", "KVM", true},
		{"Xen", "HVM domU", true},
		{"Parallels Software International Inc.", "Parallels ARM Virtual Machine", true},
		{"OpenStack Foundation", "OpenStack Nova", true},
		{"Nutanix", "AHV", true},
		{"FreeBSD", "BHYVE", true},
		{"Bochs", "Bochs", true},
		{"Google", "Google Compute Engine", true},
		{"Alibaba Cloud", "Alibaba Cloud ECS", true},
		{"Amazon EC2", "m5.large", true},
		{"Amazon EC2", "i3.metal", false},
		{"Amazon EC2", "c7i.metal-24xl", false},
		{"  vmWARE, Inc. ", "guest", true},
		{"Dell Inc.", "PowerEdge R750", false},
		{"HP", "ProLiant DL380 Gen10", false},
		{"Microsoft Corporation", "Surface Pro 9", false},
		{"LENOVO", "ThinkPad T14", false},
	} {
		t.Run(tc.manufacturer+"/"+tc.model, func(t *testing.T) {
			called := false
			err := preparePhysicalMachine(context.Background(), func() (string, string, error) {
				return tc.manufacturer, tc.model, nil
			}, func(context.Context) error {
				called = true
				return nil
			})
			if err != nil || called == tc.virtual {
				t.Fatalf("setup called=%v, virtual=%v, error=%v", called, tc.virtual, err)
			}
		})
	}
}

func TestPawnIOHardwareIdentificationFailures(t *testing.T) {
	readError := errors.New("hardware registry unavailable")
	for _, tc := range []struct {
		manufacturer, model string
		err                 error
	}{
		{"", "", readError},
		{"", "PowerEdge", nil},
		{"Dell", "  ", nil},
	} {
		err := preparePhysicalMachine(context.Background(), func() (string, string, error) {
			return tc.manufacturer, tc.model, tc.err
		}, func(context.Context) error {
			t.Fatal("setup ran without identifying hardware")
			return nil
		})
		if err == nil || tc.err != nil && !errors.Is(err, tc.err) {
			t.Fatalf("identification error lost: %v", err)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := preparePhysicalMachine(ctx, nil, nil); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation lost: %v", err)
	}
	setupError := errors.New("setup failed")
	if err := preparePhysicalMachine(context.Background(), func() (string, string, error) {
		return "Dell", "PowerEdge", nil
	}, func(context.Context) error { return setupError }); !errors.Is(err, setupError) {
		t.Fatalf("setup error lost: %v", err)
	}
}

func TestPawnIOVersionCompatibility(t *testing.T) {
	for _, version := range []string{"2.1.0", "2.1.0.0", "2.1.1", "2.2.0", "3.0.0"} {
		if !compatiblePawnIO(version) {
			t.Errorf("compatible version rejected: %q", version)
		}
	}
	for _, version := range []string{"", "1.9.9", "2.0.9", "2", "broken", "2.-1.0", "2.1.bad", "2.1.0.0.1"} {
		if compatiblePawnIO(version) {
			t.Errorf("incompatible version accepted: %q", version)
		}
	}
}

func TestPawnIOSetupInstallsMissingOrOldAndKeepsNewer(t *testing.T) {
	for _, existing := range []string{"", "2.0.0", "2.1.0", "2.2.0", "3.0.0"} {
		t.Run(existing, func(t *testing.T) {
			installed := existing
			calls := 0
			err := preparePawnIO(context.Background(), func() (string, error) { return installed, nil }, func(ctx context.Context) error {
				calls++
				deadline, ok := ctx.Deadline()
				if !ok || time.Until(deadline) > 2*time.Minute {
					t.Fatal("installer is not bounded to two minutes")
				}
				installed = PawnIOVersion
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			if compatiblePawnIO(existing) {
				if calls != 0 || installed != existing {
					t.Fatal("existing compatible driver was replaced")
				}
			} else if calls != 1 || installed != PawnIOVersion {
				t.Fatal("missing or old driver was not installed")
			}
		})
	}
}

func TestPawnIOSetupFailuresAndCancellation(t *testing.T) {
	readError := errors.New("registry access denied")
	installError := errors.New("installer failed")
	if err := preparePawnIO(context.Background(), func() (string, error) { return "", readError }, func(context.Context) error {
		t.Fatal("installer ran after a registry read failure")
		return nil
	}); !errors.Is(err, readError) {
		t.Fatalf("registry error lost: %v", err)
	}
	if err := preparePawnIO(context.Background(), func() (string, error) { return "", nil }, func(context.Context) error {
		return installError
	}); !errors.Is(err, installError) {
		t.Fatalf("installation error lost: %v", err)
	}
	if err := preparePawnIO(context.Background(), func() (string, error) { return "", nil }, func(context.Context) error {
		return nil
	}); err == nil {
		t.Fatal("installer success was accepted without verifying installed version")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := preparePawnIO(ctx, func() (string, error) {
		t.Fatal("setup continued after cancellation")
		return "", nil
	}, nil); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancellation lost: %v", err)
	}
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	if err := preparePawnIO(ctx, func() (string, error) { return "", nil }, func(ctx context.Context) error {
		cancel()
		<-ctx.Done()
		return ctx.Err()
	}); !errors.Is(err, context.Canceled) {
		t.Fatalf("installer cancellation lost: %v", err)
	}
}
