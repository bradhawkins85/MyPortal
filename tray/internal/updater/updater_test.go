package updater

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestJitteredInterval(t *testing.T) {
	// jitteredInterval must return a value within ±10 % of UpdateInterval.
	min := time.Duration(float64(UpdateInterval) * (1 - jitterFraction))
	max := time.Duration(float64(UpdateInterval) * (1 + jitterFraction))

	// Run many samples to exercise the full range.
	for i := 0; i < 1000; i++ {
		d := jitteredInterval()
		if d < min || d > max {
			t.Fatalf("jitteredInterval() = %v; want in [%v, %v]", d, min, max)
		}
	}
}

func TestJitteredIntervalNonZero(t *testing.T) {
	d := jitteredInterval()
	if d <= 0 {
		t.Fatalf("jitteredInterval() = %v; want > 0", d)
	}
}

func TestStartupJitterMaxLessThanUpdateInterval(t *testing.T) {
	// startupJitterMax must be less than UpdateInterval to avoid a situation
	// where the startup delay is longer than one full update cycle.
	if startupJitterMax >= UpdateInterval {
		t.Fatalf("startupJitterMax (%v) must be < UpdateInterval (%v)", startupJitterMax, UpdateInterval)
	}
}

func TestIsNewer(t *testing.T) {
	cases := []struct {
		available, current string
		want               bool
	}{
		{"0.8.5", "0.8.4", true},
		{"0.10.0", "0.9.9", true},
		{"v1.0.0", "0.9.0", true},
		{"0.8.4", "0.8.4", false},
		{"0.8.3", "0.8.4", false},
		{"0.8.4", "0.8.4-dev", false},
		{"0.8.4.1", "0.8.4", true},
		{"0.8", "0.8.0", false},
		{"0.0.0", "0.8.4", false},
		{"", "0.8.4", false},
		{"nightly", "0.8.4", true},
	}
	for _, tc := range cases {
		if got := isNewer(tc.available, tc.current); got != tc.want {
			t.Errorf("isNewer(%q, %q) = %v, want %v", tc.available, tc.current, got, tc.want)
		}
	}
}

func TestInstallerCommandPassesSettings(t *testing.T) {
	cmd := installerCommand("/tmp/x.msi", "https://portal.example.com", false, "/logs/update.log")
	args := strings.Join(cmd.Args, " ")
	if runtime.GOOS == "windows" {
		for _, want := range []string{"/qn", "MYPORTAL_URL=https://portal.example.com", "AUTO_UPDATE=false", "/l*v /logs/update.log"} {
			if !strings.Contains(args, want) {
				t.Errorf("installer args %q missing %q", args, want)
			}
		}
		if strings.Contains(args, "ENROL_TOKEN") {
			t.Errorf("installer args %q must not carry the enrolment token", args)
		}
	} else {
		if cmd.Path != "/usr/sbin/installer" {
			t.Errorf("installer path = %q", cmd.Path)
		}
		if want := "-pkg /tmp/x.msi -target / -verbose -dumplog"; !strings.Contains(args, want) {
			t.Errorf("installer args %q missing %q", args, want)
		}
	}
	if cmd.SysProcAttr == nil {
		t.Error("installer is not detached from the service")
	}
}

func TestCaptureInstallerOutputWritesLog(t *testing.T) {
	if runtime.GOOS == "windows" {
		if f := captureInstallerOutput(exec.Command("cmd"), filepath.Join(t.TempDir(), "u.log")); f != nil {
			t.Fatal("msiexec writes its own log; output must not be captured on Windows")
		}
		return
	}
	logPath := filepath.Join(t.TempDir(), "update-installer.log")
	if err := os.WriteFile(logPath, []byte("previous update\n"), 0600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("/bin/sh", "-c", "echo out; echo err >&2")
	f := captureInstallerOutput(cmd, logPath)
	if f == nil {
		t.Fatal("installer output is not captured")
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	// The updater closes its handle after starting, before the installer ends.
	f.Close()
	if err := cmd.Wait(); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	if got := string(data); got != "out\nerr\n" {
		t.Fatalf("installer log = %q, want only this update's output", got)
	}
}

func TestCaptureInstallerOutputWithoutLogPath(t *testing.T) {
	cmd := exec.Command("installer")
	if f := captureInstallerOutput(cmd, ""); f != nil || cmd.Stdout != nil {
		t.Fatal("output captured without a log path")
	}
}

func TestRemoveStaleInstallers(t *testing.T) {
	dir := t.TempDir()
	stale := filepath.Join(dir, installerPrefix+"old.msi")
	fresh := filepath.Join(dir, installerPrefix+"new.msi")
	other := filepath.Join(dir, "unrelated.msi")
	for _, p := range []string{stale, fresh, other} {
		if err := os.WriteFile(p, []byte("x"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	old := time.Now().Add(-2 * time.Hour)
	_ = os.Chtimes(stale, old, old)
	_ = os.Chtimes(other, old, old)

	removeStaleInstallers(dir, time.Hour)

	if _, err := os.Stat(stale); !os.IsNotExist(err) {
		t.Error("stale installer was not removed")
	}
	for _, p := range []string{fresh, other} {
		if _, err := os.Stat(p); err != nil {
			t.Errorf("%s was removed: %v", p, err)
		}
	}
}

func TestDownloadFileWritesInstaller(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("installer bytes"))
	}))
	defer srv.Close()
	dest := filepath.Join(t.TempDir(), "x.msi")
	if err := downloadFile(context.Background(), srv.URL, dest); err != nil {
		t.Fatalf("downloadFile: %v", err)
	}
	data, _ := os.ReadFile(dest)
	if string(data) != "installer bytes" {
		t.Fatalf("downloaded %q", data)
	}
}
