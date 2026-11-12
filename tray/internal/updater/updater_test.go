package updater

import (
	"context"
	"crypto/ed25519"
	crand "crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/release"
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
		{"nightly", "0.8.4", false},
		{"0.9.0", "dev", false},
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
	} else if runtime.GOOS == "linux" {
		if want := "--upgrade myportal-tray-update /tmp/x.msi /logs/update.log"; !strings.Contains(args, want) {
			t.Errorf("installer args %q missing %q", args, want)
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

func TestLinuxUpdateCommandLeavesTheServiceCgroup(t *testing.T) {
	withSystemd := linuxUpdateCommand("/tmp/u.tar.gz", "/logs/u.log", func(string) (string, error) {
		return "/usr/bin/systemd-run", nil
	})
	if withSystemd.Args[0] != "/usr/bin/systemd-run" || withSystemd.Args[5] != "/bin/sh" {
		t.Fatalf("update does not run in its own systemd unit: %q", withSystemd.Args)
	}
	without := linuxUpdateCommand("/tmp/u.tar.gz", "", func(string) (string, error) {
		return "", exec.ErrNotFound
	})
	if without.Args[0] != "/bin/sh" || without.Args[len(without.Args)-2] != "/tmp/u.tar.gz" {
		t.Fatalf("unexpected fallback command: %q", without.Args)
	}
}

func signedInstaller(t *testing.T, version string) (path, digest, signature string, restore func()) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(crand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	restore = release.UseKeyForTesting(pub)
	path = filepath.Join(t.TempDir(), "installer")
	body := []byte("installer bytes")
	if err := os.WriteFile(path, body, 0o600); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256(body)
	digest = hex.EncodeToString(sum[:])
	signature = base64.StdEncoding.EncodeToString(ed25519.Sign(priv, release.Manifest(installerAssetName(runtime.GOOS), version, digest)))
	return path, digest, signature, restore
}

func TestVerifyInstallerNeedsTheReleaseSignature(t *testing.T) {
	path, digest, sig, restore := signedInstaller(t, "v1.4.0")
	defer restore()

	if err := verifyInstaller(path, &api.VersionResponse{Version: "1.4.0", Sha256: digest, Signature: sig}); err != nil {
		t.Fatalf("signed installer refused: %v", err)
	}
	if err := verifyInstaller(path, &api.VersionResponse{Version: "1.4.0", Signature: sig}); err != nil {
		t.Fatalf("a signed installer needs no server digest: %v", err)
	}
	refused := map[string]*api.VersionResponse{
		"unsigned":        {Version: "1.4.0", Sha256: digest},
		"other version":   {Version: "1.5.0", Sha256: digest, Signature: sig},
		"digest mismatch": {Version: "1.4.0", Sha256: strings.Repeat("0", 64), Signature: sig},
		"other signature": {Version: "1.4.0", Signature: base64.StdEncoding.EncodeToString(make([]byte, 64))},
	}
	for name, resp := range refused {
		if err := verifyInstaller(path, resp); err == nil {
			t.Errorf("%s: installer accepted", name)
		}
	}
}

func TestCheckDownloadURLAllowsOnlyWebAddresses(t *testing.T) {
	for _, ok := range []string{"https://portal.example/static/tray/myportal-tray.msi", "http://portal.lan/x.msi"} {
		if err := checkDownloadURL(ok); err != nil {
			t.Errorf("%s refused: %v", ok, err)
		}
	}
	for _, bad := range []string{"file:///C:/evil.msi", `\\server\share\evil.msi`, "ftp://x/y", "https://"} {
		if err := checkDownloadURL(bad); err == nil {
			t.Errorf("%s accepted", bad)
		}
	}
}
