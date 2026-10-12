package updater

import (
	"context"
	"net/http"
	"net/http/httptest"
	"os"
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
	} else if cmd.Path != "/usr/sbin/installer" {
		t.Errorf("installer path = %q", cmd.Path)
	}
	if cmd.SysProcAttr == nil {
		t.Error("installer is not detached from the service")
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
