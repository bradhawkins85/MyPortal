//go:build windows

package healthcheck

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

const platformName = "windows"

//go:embed health_windows.ps1
var healthScript string

//go:embed sensors_windows.ps1
var sensorsScript string

// Output caps for the PowerShell scripts.
const (
	maxSnapshotOutput = 8 << 20
	maxSensorOutput   = 2 << 20
)

// protectedDirSDDL gives only SYSTEM and Administrators access, so nobody
// else can replace a bundled DLL or the installer before they are used.
const protectedDirSDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"

// limitedBuffer keeps at most limit bytes and discards the rest, so an
// unexpected flood of output cannot use unbounded memory.
type limitedBuffer struct {
	bytes.Buffer
	limit int
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	n := len(p)
	if remaining := b.limit - b.Len(); remaining > 0 {
		if len(p) > remaining {
			p = p[:remaining]
		}
		_, _ = b.Buffer.Write(p)
	}
	return n, nil
}

func systemRoot() string {
	if root := os.Getenv("SystemRoot"); root != "" {
		return root
	}
	return `C:\Windows`
}

// runPowerShell runs a fixed script with Windows PowerShell. The script is
// passed on standard input (it is longer than a command line may be) to a
// small bootstrap that runs it; nothing is written to disk.
func runPowerShell(ctx context.Context, script string, env []string, limit int) ([]byte, error) {
	exe := filepath.Join(systemRoot(), "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	bootstrap := "$s = [Console]::In.ReadToEnd(); & ([scriptblock]::Create($s))"
	units := utf16.Encode([]rune(bootstrap))
	raw := make([]byte, len(units)*2)
	for i, unit := range units {
		raw[i*2] = byte(unit)
		raw[i*2+1] = byte(unit >> 8)
	}
	cmd := exec.CommandContext(ctx, exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
		"-EncodedCommand", base64.StdEncoding.EncodeToString(raw))
	cmd.Stdin = strings.NewReader(script)
	cmd.Env = append(os.Environ(), env...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	stdout := &limitedBuffer{limit: limit}
	stderr := &limitedBuffer{limit: 16 << 10}
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	err := cmd.Run()
	out := bytes.TrimSpace(bytes.TrimPrefix(stdout.Bytes(), []byte("\xef\xbb\xbf")))
	if err != nil && len(out) == 0 {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("timed out: %w", ctx.Err())
		}
		detail := strings.Join(strings.Fields(stderr.String()), " ")
		return nil, fmt.Errorf("%w: %s", err, truncate(detail, 400))
	}
	return out, nil
}

func collectPlatformSnapshot(ctx context.Context, eventDays int) (platformSnapshot, error) {
	var snapshot platformSnapshot
	out, err := runPowerShell(ctx, healthScript, []string{fmt.Sprintf("MYPORTAL_HEALTH_DAYS=%d", eventDays)}, maxSnapshotOutput)
	if err != nil {
		return snapshot, err
	}
	// The JSON is the last line; anything a module printed before it is ignored.
	if i := bytes.LastIndex(out, []byte("\n{")); i >= 0 {
		out = out[i+1:]
	}
	if err := json.Unmarshal(out, &snapshot); err != nil {
		return snapshot, fmt.Errorf("reading the health metrics: %w", err)
	}
	return snapshot, nil
}

func platformIdentity() (string, string, error) {
	key, err := registry.OpenKey(registry.LOCAL_MACHINE, `HARDWARE\DESCRIPTION\System\BIOS`, registry.QUERY_VALUE)
	if err != nil {
		return "", "", err
	}
	defer key.Close()
	manufacturer, _, err := key.GetStringValue("SystemManufacturer")
	if err != nil {
		return "", "", err
	}
	model, _, err := key.GetStringValue("SystemProductName")
	return manufacturer, model, err
}

// pawnIOUninstallKey reads PawnIO's uninstall registration in either
// registry view. found is false when PawnIO is not registered.
func pawnIOUninstallValue(name string) (string, bool, error) {
	for _, view := range []uint32{registry.WOW64_64KEY, registry.WOW64_32KEY} {
		key, err := registry.OpenKey(registry.LOCAL_MACHINE,
			`SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO`, registry.QUERY_VALUE|view)
		if errors.Is(err, registry.ErrNotExist) {
			continue
		}
		if err != nil {
			return "", false, err
		}
		value, _, err := key.GetStringValue(name)
		key.Close()
		if errors.Is(err, registry.ErrNotExist) {
			return "", true, nil
		}
		if err != nil {
			return "", true, err
		}
		return value, true, nil
	}
	return "", false, nil
}

func pawnIOInstalledVersion() (string, error) {
	version, found, err := pawnIOUninstallValue("DisplayVersion")
	if err != nil || !found {
		return "", err
	}
	if version == "" {
		// Registered without a version: treat as installed by someone else.
		return "unknown", nil
	}
	return version, nil
}

func pawnIOUninstallCommand() (string, error) {
	for _, name := range []string{"QuietUninstallString", "UninstallString"} {
		command, found, err := pawnIOUninstallValue(name)
		if err != nil {
			return "", err
		}
		if !found {
			return "", nil
		}
		if command != "" {
			return command, nil
		}
	}
	return "", errors.New("PawnIO is registered without an uninstall command")
}

func runUninstaller(ctx context.Context, program, args string) error {
	cmd := exec.CommandContext(ctx, program)
	// Pass the registered arguments through unchanged, as Windows would.
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CmdLine: syscall.EscapeArg(program) + " " + args}
	return cmd.Run()
}

// createProtectedDir creates a new folder only SYSTEM and Administrators can use.
func createProtectedDir(path string) error {
	sd, err := windows.SecurityDescriptorFromString(protectedDirSDDL)
	if err != nil {
		return err
	}
	sa := &windows.SecurityAttributes{
		Length:             uint32(unsafe.Sizeof(windows.SecurityAttributes{})),
		SecurityDescriptor: sd,
	}
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return err
	}
	return windows.CreateDirectory(name, sa)
}

// extractBundle writes the embedded sensor bundle to a new protected folder
// and returns it with a function that deletes it.
func extractBundle(stateDir string) (string, func(), error) {
	files, err := bundleFiles()
	if err != nil {
		return "", func() {}, err
	}
	if stateDir == "" {
		return "", func() {}, errors.New("no state folder")
	}
	if err := os.MkdirAll(stateDir, 0o700); err != nil {
		return "", func() {}, err
	}
	suffix := make([]byte, 8)
	if _, err := rand.Read(suffix); err != nil {
		return "", func() {}, err
	}
	dir := filepath.Join(stateDir, sensorBundleDirPrefix+hex.EncodeToString(suffix))
	if err := createProtectedDir(dir); err != nil {
		return "", func() {}, fmt.Errorf("creating the sensor bundle folder: %w", err)
	}
	cleanup := func() { _ = os.RemoveAll(dir) }
	for name, data := range files {
		if err := os.WriteFile(filepath.Join(dir, name), data, 0o600); err != nil {
			cleanup()
			return "", func() {}, fmt.Errorf("extracting the sensor bundle: %w", err)
		}
	}
	return dir, cleanup, nil
}

// bundleFiles returns the embedded bundle's DLLs and installer after checking
// the installer against its pinned hash.
func bundleFiles() (map[string][]byte, error) {
	entries, err := sensorBundle.ReadDir(sensorBundleRoot)
	if err != nil {
		return nil, fmt.Errorf("this tray build has no sensor bundle: %w", err)
	}
	files := map[string][]byte{}
	for _, entry := range entries {
		name := entry.Name()
		lower := strings.ToLower(name)
		if entry.IsDir() || !(strings.HasSuffix(lower, ".dll") || lower == "pawnio_setup.exe") {
			continue
		}
		data, err := sensorBundle.ReadFile(sensorBundleRoot + "/" + name)
		if err != nil {
			return nil, err
		}
		files[name] = data
	}
	installer, ok := files["PawnIO_setup.exe"]
	if _, lib := files["LibreHardwareMonitorLib.dll"]; !ok || !lib {
		return nil, errors.New("this tray build does not include the sensor bundle")
	}
	if sum := sha256.Sum256(installer); hex.EncodeToString(sum[:]) != pawnIOInstallerSHA256 {
		return nil, errors.New("the bundled PawnIO installer does not match the pinned version")
	}
	return files, nil
}

func readSensors(ctx context.Context, dir string) ([]SensorReading, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	out, err := runPowerShell(ctx, sensorsScript,
		[]string{"MYPORTAL_SENSOR_DLL=" + filepath.Join(dir, "LibreHardwareMonitorLib.dll")}, maxSensorOutput)
	if err != nil {
		return nil, err
	}
	if i := bytes.LastIndex(out, []byte("\n[")); i >= 0 {
		out = out[i+1:]
	}
	var readings []SensorReading
	if err := json.Unmarshal(out, &readings); err != nil {
		return nil, fmt.Errorf("reading the sensor values: %w", err)
	}
	return cleanReadings(readings), nil
}

func platformSensors(ctx context.Context, stateDir string, allow bool, identity machineIdentity) (SensorReport, PawnIOReport) {
	dir, cleanup, err := extractBundle(stateDir)
	if err != nil {
		sensors := SensorReport{Status: "unavailable", Source: "LibreHardwareMonitor", Detail: truncate(err.Error(), 300), Readings: []SensorReading{}}
		switch {
		case !allow:
			return sensors, PawnIOReport{Status: PawnIOSkippedDisabled}
		case identity.Err == nil && isVirtualMachine(identity.Manufacturer, identity.Model):
			return sensors, PawnIOReport{Status: PawnIOSkippedVirtual}
		default:
			return sensors, PawnIOReport{Status: PawnIOBundleMissing, Detail: truncate(err.Error(), 300)}
		}
	}
	defer cleanup()
	ops := pawnIOOps{
		installedVersion: pawnIOInstalledVersion,
		install: func(ctx context.Context) error {
			ctx, cancel := context.WithTimeout(ctx, 3*time.Minute)
			defer cancel()
			cmd := exec.CommandContext(ctx, filepath.Join(dir, "PawnIO_setup.exe"), "-install", "-silent")
			cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
			return cmd.Run()
		},
		uninstallCommand: pawnIOUninstallCommand,
		runUninstaller:   runUninstaller,
		readSensors:      func(ctx context.Context) ([]SensorReading, error) { return readSensors(ctx, dir) },
		writeMarker:      func() error { return writePawnIOMarker(stateDir, time.Now()) },
		removeMarker:     func() error { return removePawnIOMarker(stateDir) },
	}
	return collectSensors(ctx, allow, identity, ops)
}

func platformCleanupPawnIO(ctx context.Context, stateDir string) (bool, error) {
	removeStaleBundles(stateDir)
	return cleanupPawnIO(ctx, stateDir, pawnIOUninstallCommand, runUninstaller)
}
