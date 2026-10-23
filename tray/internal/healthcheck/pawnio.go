package healthcheck

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// PawnIO is the signed kernel driver LibreHardwareMonitor uses to read CPU
// and motherboard sensors. The MyPortal RMM agent once kept it installed and
// those machines reported SYSTEM_THREAD_EXCEPTION_NOT_HANDLED (0x7E) crashes,
// so the health check only installs it on physical hardware, only for the
// length of one check, and removes it straight afterwards. A marker file in
// the state folder records the installation until removal is confirmed, so a
// service that stops part-way removes the driver when it next starts
// (CleanupPawnIO). The RMM agent also removes PawnIO whenever it starts.

// pawnIOInstallerSHA256 pins the bundled PawnIO 2.1.0 installer
// (sensor-bundle/build.py downloads and checks the same hash).
const pawnIOInstallerSHA256 = "a3a46226c5e2824f4cdd42be0eecbabfc672c86f7889710f5ab1e6ad385b47a0"

// pawnIOMarkerName is the state-folder file that exists while the health
// check's PawnIO installation has not been confirmed removed.
const pawnIOMarkerName = "pawnio-health-check.json"

// removalTimeout bounds uninstalling the driver. Removal never uses the
// check's own context, which may already be cancelled.
const removalTimeout = 3 * time.Minute

// isVirtualMachine matches guest hardware identifiers rather than
// HypervisorPresent: physical hosts with Hyper-V or virtualization-based
// security still have real sensors.
func isVirtualMachine(manufacturer, model string) bool {
	identity := strings.ToLower(manufacturer + " " + model)
	for _, marker := range []string{
		"virtual machine", "vmware", "virtualbox", "innotek", "kvm", "qemu",
		"xen", "hvm domu", "parallels", "bhyve", "bochs", "openstack", "nutanix",
		"google compute engine", "alibaba cloud", "virtualmac",
	} {
		if strings.Contains(identity, marker) {
			return true
		}
	}
	// EC2 bare-metal instances expose physical hardware and can have sensors.
	return strings.Contains(identity, "amazon ec2") && !strings.Contains(strings.ToLower(model), ".metal")
}

// compatiblePawnIO reports whether an installed version is 2.1 or newer.
func compatiblePawnIO(version string) bool {
	parts := strings.Split(strings.TrimSpace(version), ".")
	if len(parts) < 2 || len(parts) > 4 {
		return false
	}
	var numbers [4]int
	for i, part := range parts {
		number, err := strconv.Atoi(part)
		if err != nil || number < 0 {
			return false
		}
		numbers[i] = number
	}
	return numbers[0] > 2 || numbers[0] == 2 && numbers[1] >= 1
}

// uninstallArgs returns the program and its arguments from a registered
// uninstall command line, such as `C:\Program Files\PawnIO\uninstall.exe`,
// adding the -silent switch when the registered command does not include it.
// Mirrors rmm/internal/thermal (a separate module).
func uninstallArgs(command string) (string, string, error) {
	command = strings.TrimSpace(command)
	var program, args string
	if strings.HasPrefix(command, `"`) {
		end := strings.Index(command[1:], `"`)
		if end < 0 {
			return "", "", fmt.Errorf("unterminated quote in uninstall command %q", command)
		}
		program, args = command[1:end+1], command[end+2:]
	} else if end := strings.Index(strings.ToLower(command), ".exe"); end >= 0 {
		program, args = command[:end+4], command[end+4:]
	} else {
		program, args = command, ""
	}
	if program == "" || !strings.HasSuffix(strings.ToLower(program), ".exe") {
		return "", "", fmt.Errorf("unexpected uninstall command %q", command)
	}
	args = strings.TrimSpace(args)
	if !strings.Contains(strings.ToLower(args), "-silent") {
		args = strings.TrimSpace(args + " -silent")
	}
	return program, args, nil
}

// removePawnIO runs PawnIO's registered uninstaller and confirms it is gone.
// uninstallCommand returns "" when PawnIO is not registered.
func removePawnIO(ctx context.Context, uninstallCommand func() (string, error), run func(context.Context, string, string) error) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	command, err := uninstallCommand()
	if err != nil {
		return fmt.Errorf("reading PawnIO uninstall command: %w", err)
	}
	if command == "" {
		return nil
	}
	program, args, err := uninstallArgs(command)
	if err != nil {
		return err
	}
	if err := run(ctx, program, args); err != nil {
		return fmt.Errorf("uninstalling PawnIO: %w", err)
	}
	command, err = uninstallCommand()
	if err != nil {
		return fmt.Errorf("verifying PawnIO removal: %w", err)
	}
	if command != "" {
		return errors.New("PawnIO is still registered after uninstalling")
	}
	return nil
}

// pawnIOOps are the platform operations the lifecycle needs. Tests replace
// them so no driver is installed.
type pawnIOOps struct {
	installedVersion func() (string, error) // "" when PawnIO is not installed
	install          func(ctx context.Context) error
	uninstallCommand func() (string, error) // "" when PawnIO is not registered
	runUninstaller   func(ctx context.Context, program, args string) error
	readSensors      func(ctx context.Context) ([]SensorReading, error)
	writeMarker      func() error
	removeMarker     func() error
}

// collectSensors reads hardware sensors, installing PawnIO first when it is
// allowed, the computer is physical and PawnIO is not already installed, and
// always removing an installation it made before returning.
func collectSensors(ctx context.Context, allow bool, identity machineIdentity, ops pawnIOOps) (sensors SensorReport, pawn PawnIOReport) {
	sensors = SensorReport{Status: "unavailable", Source: "LibreHardwareMonitor", Readings: []SensorReading{}}
	installed := false
	switch {
	case !allow:
		pawn = PawnIOReport{Status: PawnIOSkippedDisabled}
	case identity.Err != nil:
		pawn = PawnIOReport{Status: PawnIOIdentifyFailed, Detail: truncate(identity.Err.Error(), 300)}
	case strings.TrimSpace(identity.Manufacturer) == "" || strings.TrimSpace(identity.Model) == "":
		pawn = PawnIOReport{Status: PawnIOIdentifyFailed, Detail: "missing manufacturer or model"}
	case isVirtualMachine(identity.Manufacturer, identity.Model):
		pawn = PawnIOReport{Status: PawnIOSkippedVirtual}
	default:
		pawn, installed = installPawnIO(ctx, ops)
	}
	if installed {
		// Deferred so a failure while reading sensors still removes it.
		defer func() {
			removeCtx, cancel := context.WithTimeout(context.Background(), removalTimeout)
			defer cancel()
			if err := removePawnIO(removeCtx, ops.uninstallCommand, ops.runUninstaller); err != nil {
				pawn = PawnIOReport{Status: PawnIORemovalFailed, Detail: truncate(err.Error(), 300)}
				return
			}
			if err := ops.removeMarker(); err != nil && !errors.Is(err, os.ErrNotExist) {
				pawn.Detail = strings.TrimSpace(pawn.Detail + " (the cleanup marker could not be deleted)")
			}
		}()
	}
	if ctx.Err() != nil {
		sensors.Detail = "the check ran out of time before sensors were read"
		return sensors, pawn
	}
	readings, err := ops.readSensors(ctx)
	switch {
	case err != nil:
		sensors.Status = "error"
		sensors.Detail = truncate(err.Error(), 300)
	case len(readings) > 0:
		sensors.Status = "available"
		sensors.Readings = readings
	default:
		sensors.Detail = "no sensors reported a value"
	}
	return sensors, pawn
}

// installPawnIO installs the driver unless it is already present. It returns
// true whenever this check may have left PawnIO installed (even a failed
// install is removed again).
func installPawnIO(ctx context.Context, ops pawnIOOps) (PawnIOReport, bool) {
	version, err := ops.installedVersion()
	if err != nil {
		return PawnIOReport{Status: PawnIOInstallFailed, Detail: truncate("reading the installed version: "+err.Error(), 300)}, false
	}
	if version != "" {
		return PawnIOReport{Status: PawnIOAlreadyInstalled, Detail: "version " + truncate(version, 40)}, false
	}
	// Record the installation before it starts, so an interrupted check is
	// cleaned up when the service next starts.
	if err := ops.writeMarker(); err != nil {
		return PawnIOReport{Status: PawnIOInstallFailed, Detail: truncate("recording the installation: "+err.Error(), 300)}, false
	}
	if err := ops.install(ctx); err != nil {
		return PawnIOReport{Status: PawnIOInstallFailed, Detail: truncate(err.Error(), 300)}, true
	}
	version, err = ops.installedVersion()
	if err != nil || !compatiblePawnIO(version) {
		return PawnIOReport{Status: PawnIOInstallFailed, Detail: "the installer did not register PawnIO 2.1 or newer"}, true
	}
	return PawnIOReport{Status: PawnIOInstalledAndRemoved, Detail: "version " + truncate(version, 40)}, true
}

// pawnIOMarker is the content of the marker file.
type pawnIOMarker struct {
	InstalledAt string `json:"installed_at"`
}

func writePawnIOMarker(stateDir string, now time.Time) error {
	if stateDir == "" {
		return errors.New("no state folder")
	}
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return err
	}
	data, _ := json.Marshal(pawnIOMarker{InstalledAt: now.UTC().Format(time.RFC3339)})
	return os.WriteFile(filepath.Join(stateDir, pawnIOMarkerName), data, 0o600)
}

func removePawnIOMarker(stateDir string) error {
	return os.Remove(filepath.Join(stateDir, pawnIOMarkerName))
}

func pawnIOMarkerExists(stateDir string) bool {
	if stateDir == "" {
		return false
	}
	_, err := os.Stat(filepath.Join(stateDir, pawnIOMarkerName))
	return err == nil
}

// cleanupPawnIO removes a PawnIO installation a health check left behind.
func cleanupPawnIO(ctx context.Context, stateDir string, uninstallCommand func() (string, error), run func(context.Context, string, string) error) (bool, error) {
	if !pawnIOMarkerExists(stateDir) {
		return false, nil
	}
	removeCtx, cancel := context.WithTimeout(ctx, removalTimeout)
	defer cancel()
	if err := removePawnIO(removeCtx, uninstallCommand, run); err != nil {
		return true, err
	}
	if err := removePawnIOMarker(stateDir); err != nil && !errors.Is(err, os.ErrNotExist) {
		return true, err
	}
	return true, nil
}
