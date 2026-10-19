//go:build windows

package thermal

import (
	"bytes"
	"context"
	"crypto/sha256"
	"fmt"
	"os"
	"os/exec"
	"testing"
	"time"
)

func TestBundleExtractionVerificationAndLibraryLoading(t *testing.T) {
	expected, err := bundle.ReadFile("bundle/LibreHardwareMonitorLib.dll")
	if err != nil {
		t.Skip("prepare the Windows bundle with thermal-bundle/build.py")
	}
	t.Setenv("MYPORTAL_RMM_STATE_DIR", t.TempDir())
	dll, err := bundlePath()
	if err != nil {
		t.Fatal(err)
	}
	actual, err := os.ReadFile(dll)
	if err != nil || !bytes.Equal(expected, actual) {
		t.Fatal("extracted DLL differs from bundled DLL")
	}
	installer, err := bundle.ReadFile("bundle/PawnIO_setup.exe")
	if err != nil {
		t.Fatal("bundled PawnIO installer missing")
	}
	if fmt.Sprintf("%x", sha256.Sum256(installer)) != "a3a46226c5e2824f4cdd42be0eecbabfc672c86f7889710f5ab1e6ad385b47a0" {
		t.Fatal("bundled PawnIO installer differs from pinned upstream 2.1.0")
	}
	again, err := bundlePath()
	if err != nil || again != dll {
		t.Fatalf("bundle reuse: %q %v", again, err)
	}
	cmd := exec.Command("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; Add-Type -Path $env:MYPORTAL_THERMAL_DLL; $computer=New-Object LibreHardwareMonitor.Hardware.Computer; $computer.IsCpuEnabled=$true")
	cmd.Env = append(os.Environ(), "MYPORTAL_THERMAL_DLL="+dll)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("load embedded library with Windows PowerShell: %v %s", err, output)
	}
	snapshot := Collect(context.Background())
	if snapshot.Status == "error" {
		t.Fatal("bundled collector failed on Windows")
	}
	if err := os.WriteFile(dll, []byte("corrupted"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := bundlePath(); err == nil {
		t.Fatal("corrupted library accepted")
	}
}

func TestOutputLimit(t *testing.T) {
	var output limitedOutput
	input := bytes.Repeat([]byte("x"), 2<<20)
	if n, err := output.Write(input); n != len(input) || err != nil || output.Len() != 1<<20 {
		t.Fatal("incorrect output bound")
	}
}

// This changes system driver state and runs only on the explicitly opted-in
// disposable Windows CI runner, never during ordinary local go test runs.
func TestWindowsPawnIOInstallation(t *testing.T) {
	if os.Getenv("MYPORTAL_TEST_PAWNIO_INSTALL") != "1" {
		t.Skip("PawnIO installation requires an explicitly opted-in Windows test host")
	}
	t.Setenv("MYPORTAL_RMM_STATE_DIR", t.TempDir())
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	// Hosted CI runners are commonly VMs. Exercise the installer explicitly;
	// production Prepare must skip driver installation on these machines.
	if err := installPhysicalPawnIO(ctx); err != nil {
		t.Fatal(err)
	}
	version, err := installedPawnIOVersion()
	if err != nil || !compatiblePawnIO(version) {
		t.Fatalf("installed driver version: %q %v", version, err)
	}
	// A repeat must preserve the installation; the policy unit tests assert
	// that the installer is not invoked for compatible versions.
	if err := installPhysicalPawnIO(ctx); err != nil {
		t.Fatalf("repeated preparation: %v", err)
	}
}
