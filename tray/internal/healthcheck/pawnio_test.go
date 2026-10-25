package healthcheck

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestVirtualMachineDetection(t *testing.T) {
	for _, tc := range []struct {
		manufacturer, model string
		virtual             bool
	}{
		{"Microsoft Corporation", "Virtual Machine", true},
		{"VMware, Inc.", "VMware Virtual Platform", true},
		{"innotek GmbH", "VirtualBox", true},
		{"QEMU", "Standard PC (Q35 + ICH9, 2009)", true},
		{"Xen", "HVM domU", true},
		{"Parallels Software International Inc.", "Parallels ARM Virtual Machine", true},
		{"Apple", "VirtualMac2,1", true},
		{"Amazon EC2", "m5.large", true},
		{"Amazon EC2", "i3.metal", false},
		{"Dell Inc.", "Latitude 7440", false},
		{"LENOVO", "ThinkPad T14", false},
		{"Microsoft Corporation", "Surface Pro 9", false},
		{"Apple", "MacBookPro18,3", false},
	} {
		if got := isVirtualMachine(tc.manufacturer, tc.model); got != tc.virtual {
			t.Errorf("%s / %s: virtual=%v, want %v", tc.manufacturer, tc.model, got, tc.virtual)
		}
	}
}

func TestPawnIOVersionCompatibility(t *testing.T) {
	for _, version := range []string{"2.1", "2.1.0", "2.1.0.0", "2.2.0", "3.0.0"} {
		if !compatiblePawnIO(version) {
			t.Errorf("compatible version rejected: %q", version)
		}
	}
	for _, version := range []string{"", "2", "1.9.9", "2.0.9", "broken", "2.-1.0", "2.1.0.0.1", "unknown"} {
		if compatiblePawnIO(version) {
			t.Errorf("incompatible version accepted: %q", version)
		}
	}
}

func TestUninstallArgs(t *testing.T) {
	for _, tc := range []struct{ command, program, args string }{
		{`c:\Program Files\PawnIO\uninstall.exe -silent`, `c:\Program Files\PawnIO\uninstall.exe`, "-silent"},
		{`c:\Program Files\PawnIO\uninstall.exe`, `c:\Program Files\PawnIO\uninstall.exe`, "-silent"},
		{`"C:\Program Files\PawnIO\PawnIO_setup.exe" -uninstall`, `C:\Program Files\PawnIO\PawnIO_setup.exe`, "-uninstall -silent"},
	} {
		program, args, err := uninstallArgs(tc.command)
		if err != nil || program != tc.program || args != tc.args {
			t.Errorf("%q: got %q %q %v", tc.command, program, args, err)
		}
	}
	for _, command := range []string{"", `"C:\PawnIO\setup.exe -uninstall`, "msiexec /x {GUID}"} {
		if _, _, err := uninstallArgs(command); err == nil {
			t.Errorf("%q accepted", command)
		}
	}
}

// fakePawnIO records what the lifecycle did, without touching a driver.
type fakePawnIO struct {
	version       string
	versionErr    error
	installErr    error
	installSets   string
	uninstallErr  error
	uninstallKeep bool
	readErr       error
	readings      []SensorReading
	markerErr     error

	installed   bool
	uninstalled bool
	marker      bool
	steps       []string
}

func (f *fakePawnIO) ops() pawnIOOps {
	return pawnIOOps{
		installedVersion: func() (string, error) {
			if f.versionErr != nil {
				return "", f.versionErr
			}
			if f.installed {
				return f.installSets, nil
			}
			return f.version, nil
		},
		install: func(context.Context) error {
			f.steps = append(f.steps, "install")
			if f.installErr != nil {
				return f.installErr
			}
			f.installed = true
			return nil
		},
		uninstallCommand: func() (string, error) {
			if f.installed || f.version != "" {
				return `C:\Program Files\PawnIO\uninstall.exe`, nil
			}
			return "", nil
		},
		runUninstaller: func(_ context.Context, program, args string) error {
			f.steps = append(f.steps, "uninstall "+args)
			if f.uninstallErr != nil {
				return f.uninstallErr
			}
			if !f.uninstallKeep {
				f.installed = false
				f.uninstalled = true
			}
			return nil
		},
		readSensors: func(context.Context) ([]SensorReading, error) {
			f.steps = append(f.steps, "read")
			return f.readings, f.readErr
		},
		writeMarker: func() error {
			if f.markerErr != nil {
				return f.markerErr
			}
			f.marker = true
			f.steps = append(f.steps, "marker")
			return nil
		},
		removeMarker: func() error {
			f.marker = false
			f.steps = append(f.steps, "unmark")
			return nil
		},
	}
}

var physical = machineIdentity{Manufacturer: "Dell Inc.", Model: "Latitude 7440"}

func reading(value float64) SensorReading {
	return SensorReading{HardwareType: "Cpu", Hardware: "CPU", Type: "Temperature", Name: "Package", Value: &value}
}

func TestSensorsInstallReadAndRemovePawnIO(t *testing.T) {
	f := &fakePawnIO{installSets: "2.1.0", readings: []SensorReading{reading(61)}}
	sensors, pawn := collectSensors(context.Background(), true, physical, f.ops())
	if pawn.Status != PawnIOInstalledAndRemoved {
		t.Fatalf("pawn status %q (%s)", pawn.Status, pawn.Detail)
	}
	if sensors.Status != "available" || len(sensors.Readings) != 1 {
		t.Fatalf("sensors %+v", sensors)
	}
	want := "marker,install,read,uninstall -silent,unmark"
	if got := strings.Join(f.steps, ","); got != want {
		t.Fatalf("steps %s, want %s", got, want)
	}
	if f.installed || f.marker {
		t.Fatal("PawnIO or its marker was left behind")
	}
}

func TestSensorsRemovePawnIOWhenReadingFails(t *testing.T) {
	f := &fakePawnIO{installSets: "2.1.0", readErr: errors.New("library failed")}
	sensors, pawn := collectSensors(context.Background(), true, physical, f.ops())
	if sensors.Status != "error" || pawn.Status != PawnIOInstalledAndRemoved || f.installed {
		t.Fatalf("sensors %+v pawn %+v installed %v", sensors, pawn, f.installed)
	}
}

func TestSensorsRemovePawnIOWhenTheCheckIsCancelled(t *testing.T) {
	f := &fakePawnIO{installSets: "2.1.0"}
	ctx, cancel := context.WithCancel(context.Background())
	ops := f.ops()
	install := ops.install
	ops.install = func(ctx context.Context) error {
		err := install(ctx)
		cancel()
		return err
	}
	_, pawn := collectSensors(ctx, true, physical, ops)
	if f.installed || f.marker || pawn.Status != PawnIOInstalledAndRemoved {
		t.Fatalf("cancelled check left PawnIO installed: %+v %v", pawn, f.steps)
	}
}

func TestSensorsCleanUpAFailedInstall(t *testing.T) {
	f := &fakePawnIO{installErr: errors.New("installer exit status 1")}
	_, pawn := collectSensors(context.Background(), true, physical, f.ops())
	if pawn.Status != PawnIOInstallFailed || !strings.Contains(pawn.Detail, "exit status 1") {
		t.Fatalf("pawn %+v", pawn)
	}
	if f.marker {
		t.Fatal("marker kept after a failed install that left nothing installed")
	}
	if strings.Join(f.steps, ",") != "marker,install,read,unmark" {
		t.Fatalf("steps %v", f.steps)
	}
}

func TestSensorsReportRemovalFailureAndKeepMarker(t *testing.T) {
	f := &fakePawnIO{installSets: "2.1.0", uninstallKeep: true}
	_, pawn := collectSensors(context.Background(), true, physical, f.ops())
	if pawn.Status != PawnIORemovalFailed || !f.marker {
		t.Fatalf("pawn %+v marker %v", pawn, f.marker)
	}
}

func TestSensorsLeaveAnExistingPawnIOAlone(t *testing.T) {
	f := &fakePawnIO{version: "2.2.0", readings: []SensorReading{reading(50)}}
	_, pawn := collectSensors(context.Background(), true, physical, f.ops())
	if pawn.Status != PawnIOAlreadyInstalled || f.uninstalled || f.marker {
		t.Fatalf("pawn %+v steps %v", pawn, f.steps)
	}
	if strings.Join(f.steps, ",") != "read" {
		t.Fatalf("steps %v", f.steps)
	}
}

func TestSensorsNeverInstallOnVirtualMachinesOrWhenDisabled(t *testing.T) {
	for name, tc := range map[string]struct {
		allow    bool
		identity machineIdentity
		status   string
	}{
		"disabled": {false, physical, PawnIOSkippedDisabled},
		"virtual":  {true, machineIdentity{Manufacturer: "VMware, Inc.", Model: "VMware7,1"}, PawnIOSkippedVirtual},
		"unknown":  {true, machineIdentity{Err: errors.New("registry unavailable")}, PawnIOIdentifyFailed},
		"no model": {true, machineIdentity{Manufacturer: "Dell Inc."}, PawnIOIdentifyFailed},
	} {
		t.Run(name, func(t *testing.T) {
			f := &fakePawnIO{installSets: "2.1.0"}
			_, pawn := collectSensors(context.Background(), tc.allow, tc.identity, f.ops())
			if pawn.Status != tc.status {
				t.Fatalf("status %q, want %q", pawn.Status, tc.status)
			}
			for _, step := range f.steps {
				if step == "install" || step == "marker" {
					t.Fatalf("PawnIO touched: %v", f.steps)
				}
			}
		})
	}
}

func TestSensorsDoNotInstallWithoutAMarker(t *testing.T) {
	f := &fakePawnIO{installSets: "2.1.0", markerErr: errors.New("read-only disk")}
	_, pawn := collectSensors(context.Background(), true, physical, f.ops())
	if pawn.Status != PawnIOInstallFailed || f.installed {
		t.Fatalf("installed without a cleanup marker: %+v", pawn)
	}
}

func TestSensorsRejectAnIncompatibleInstall(t *testing.T) {
	f := &fakePawnIO{installSets: "2.0.0"}
	_, pawn := collectSensors(context.Background(), true, physical, f.ops())
	if pawn.Status != PawnIOInstallFailed || f.installed {
		t.Fatalf("pawn %+v installed %v", pawn, f.installed)
	}
}

func TestCleanupPawnIOAfterAnInterruptedCheck(t *testing.T) {
	dir := t.TempDir()
	registered := `C:\Program Files\PawnIO\uninstall.exe`
	uninstall := func() (string, error) { return registered, nil }
	ran := 0
	run := func(context.Context, string, string) error {
		ran++
		registered = ""
		return nil
	}
	if found, err := cleanupPawnIO(context.Background(), dir, uninstall, run); found || err != nil || ran != 0 {
		t.Fatalf("no marker: found=%v err=%v ran=%d", found, err, ran)
	}
	if err := writePawnIOMarker(dir, time.Now()); err != nil {
		t.Fatal(err)
	}
	found, err := cleanupPawnIO(context.Background(), dir, uninstall, run)
	if !found || err != nil || ran != 1 {
		t.Fatalf("found=%v err=%v ran=%d", found, err, ran)
	}
	if _, err := os.Stat(filepath.Join(dir, pawnIOMarkerName)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("marker kept: %v", err)
	}
}

func TestCleanupPawnIOKeepsTheMarkerWhenRemovalFails(t *testing.T) {
	dir := t.TempDir()
	if err := writePawnIOMarker(dir, time.Now()); err != nil {
		t.Fatal(err)
	}
	uninstall := func() (string, error) { return `C:\PawnIO\uninstall.exe`, nil }
	run := func(context.Context, string, string) error { return errors.New("access denied") }
	if _, err := cleanupPawnIO(context.Background(), dir, uninstall, run); err == nil {
		t.Fatal("removal failure hidden")
	}
	if !pawnIOMarkerExists(dir) {
		t.Fatal("marker removed although PawnIO is still installed")
	}
}

func TestRemoveStaleBundles(t *testing.T) {
	dir := t.TempDir()
	stale := filepath.Join(dir, sensorBundleDirPrefix+"abc")
	if err := os.MkdirAll(stale, 0o700); err != nil {
		t.Fatal(err)
	}
	keep := filepath.Join(dir, "state.json")
	if err := os.WriteFile(keep, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
	removeStaleBundles(dir)
	if _, err := os.Stat(stale); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("stale bundle kept")
	}
	if _, err := os.Stat(keep); err != nil {
		t.Fatal("unrelated state removed")
	}
}
