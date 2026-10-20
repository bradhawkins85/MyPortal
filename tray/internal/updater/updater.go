// Package updater handles auto-update checks for the tray service.
//
// Every UpdateInterval (default 6 hours) the service calls
// GET /api/tray/version. If a newer version is available and
// AutoUpdate is enabled, the installer is downloaded and deferred
// until no interactive session is active (or forced immediately when
// Required is true on the server response).
//
// Load-balancing: two jitter mechanisms spread update checks across the
// fleet so a published version does not cause all devices to hit the
// server simultaneously:
//
//  1. Startup jitter — a random 0–30 minute sleep before the very first
//     check, so a mass reboot or new deployment does not result in a
//     thundering herd at t=0.
//  2. Per-tick jitter — each subsequent interval is ±10 % of UpdateInterval
//     (i.e. ±36 minutes for the 6-hour default), so polls drift apart
//     naturally over time.
package updater

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"io"
	"math/rand"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// AgentVersion is embedded at build time via ldflags.
var AgentVersion = "0.1.0"

// UpdateInterval is the base interval between version checks.
const UpdateInterval = 6 * time.Hour

// startupJitterMax is the upper bound of the random delay added before
// the first version check.  A device chosen uniformly at random will
// therefore fire its first check anywhere in the window [0, 30 min).
const startupJitterMax = 30 * time.Minute

// jitterFraction controls the ±percentage applied to each subsequent
// check interval.  0.10 means ±10 % of UpdateInterval.
const jitterFraction = 0.10

// jitteredInterval returns UpdateInterval with a uniform random offset in
// the range [−jitterFraction·UpdateInterval, +jitterFraction·UpdateInterval].
func jitteredInterval() time.Duration {
	spread := float64(UpdateInterval) * jitterFraction
	offset := (rand.Float64()*2 - 1) * spread // −spread … +spread
	return UpdateInterval + time.Duration(offset)
}

// Checker runs periodic update checks.
type Checker struct {
	client     *api.Client
	autoUpdate bool
	current    string
	// checking stops a scheduled check and an update command from
	// downloading and launching two installers at once.
	checking sync.Mutex
}

// New creates a new update Checker.
func New(client *api.Client, autoUpdate bool) *Checker {
	return &Checker{
		client:     client,
		autoUpdate: autoUpdate,
		current:    AgentVersion,
	}
}

// Run starts the update loop; it blocks until ctx is cancelled.
//
// A random startup jitter of 0–30 minutes is applied before the first
// check, then subsequent checks use a jittered interval (±10 %) so the
// fleet's polls spread out naturally over time.
func (c *Checker) Run(ctx context.Context) {
	// Startup jitter: sleep a random duration before the first check so
	// that devices rebooted/deployed at the same moment do not all poll
	// simultaneously.
	startupDelay := time.Duration(rand.Float64() * float64(startupJitterMax))
	logger.Info("Auto-update: first check in %v", startupDelay.Round(time.Second))
	select {
	case <-ctx.Done():
		return
	case <-time.After(startupDelay):
	}

	c.check(ctx)

	// Subsequent checks use a per-tick jittered interval so that device
	// polls drift apart over time across the fleet.
	for {
		interval := jitteredInterval()
		timer := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
			c.check(ctx)
		}
	}
}

func (c *Checker) check(ctx context.Context) {
	c.CheckNow(ctx, false)
}

// CheckNow runs an immediate update check. When force is true, the check runs
// even if automatic updates are disabled in the tray configuration.
func (c *Checker) CheckNow(ctx context.Context, force bool) {
	if !force && !c.autoUpdate {
		return
	}
	if !c.checking.TryLock() {
		logger.Info("Auto-update: a check is already running")
		return
	}
	defer c.checking.Unlock()
	resp, err := c.client.GetVersion(ctx)
	if err != nil {
		logger.Warn("Auto-update check failed: %v", err)
		return
	}
	if !isNewer(resp.Version, c.current) {
		if force {
			logger.Info("Auto-update: no newer version available (server offers %s, current %s)", resp.Version, c.current)
		}
		return
	}
	logger.Info("New tray version available: %s (current %s)", resp.Version, c.current)
	if resp.DownloadURL == "" {
		logger.Warn("Auto-update: version %s has no download URL", resp.Version)
		return
	}
	if err := c.downloadAndInstall(ctx, resp); err != nil {
		logger.Error("Auto-update failed: %v", err)
	}
}

// installerPrefix names the temporary installer files so stale ones from an
// earlier update can be found and removed.
const installerPrefix = "myportal-tray-update-"

func (c *Checker) downloadAndInstall(ctx context.Context, resp *api.VersionResponse) error {
	var ext string
	switch runtime.GOOS {
	case "windows":
		ext = ".msi"
	default:
		ext = ".pkg"
	}
	removeStaleInstallers(os.TempDir(), time.Hour)
	// Use a randomly named temp file so a local attacker cannot pre-create
	// or symlink the fixed installer path the service will execute.
	dest, err := os.CreateTemp(os.TempDir(), installerPrefix+"*"+ext)
	if err != nil {
		return err
	}
	tmpPath := dest.Name()
	dest.Close()

	logger.Info("Downloading installer to %s", tmpPath)
	if err := downloadFile(ctx, resp.DownloadURL, tmpPath); err != nil {
		os.Remove(tmpPath)
		return err
	}
	if err := verifyInstallerSha256(tmpPath, resp.Sha256); err != nil {
		os.Remove(tmpPath)
		return err
	}

	logPath := installerLogPath()
	cmd := installerCommand(tmpPath, c.client.BaseURL(), c.autoUpdate, logPath)
	installerLog := captureInstallerOutput(cmd, logPath)
	logger.Info("Launching installer for version %s: %s", resp.Version, strings.Join(cmd.Args, " "))
	err = cmd.Start()
	// The installer holds its own handle to the log; this one is not needed.
	if installerLog != nil {
		installerLog.Close()
	}
	if err != nil {
		os.Remove(tmpPath)
		return err
	}
	// The installer stops and replaces this service. Remove the download once
	// it finishes if the service is still running then; otherwise the next
	// check removes it.
	go func() {
		err := cmd.Wait()
		if err != nil {
			logger.Error("Installer for version %s failed: %v", resp.Version, err)
		}
		os.Remove(tmpPath)
	}()
	return nil
}

// installerCommand builds the silent install command for the downloaded
// installer. On Windows the portal URL and auto-update setting are passed
// to the MSI, because a major upgrade removes the old install settings
// before writing the new ones. The enrolment token is not passed: the
// device is already enrolled, and the MSI keeps the saved value itself.
// The installer runs detached from the service so it survives the service
// being stopped for the upgrade.
func installerCommand(path, portalURL string, autoUpdate bool, logPath string) *exec.Cmd {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		msiexec := "msiexec.exe"
		if root := os.Getenv("SystemRoot"); root != "" {
			msiexec = filepath.Join(root, "System32", "msiexec.exe")
		}
		args := []string{"/i", path, "/qn", "/norestart"}
		if logPath != "" {
			args = append(args, "/l*v", logPath)
		}
		if portalURL != "" {
			args = append(args, "MYPORTAL_URL="+portalURL)
		}
		args = append(args, "AUTO_UPDATE="+strconv.FormatBool(autoUpdate))
		cmd = exec.Command(msiexec, args...)
	default:
		// -dumplog adds the detailed install log, including the package
		// scripts' output, to the captured output.
		cmd = exec.Command("/usr/sbin/installer", "-pkg", path, "-target", "/", "-verbose", "-dumplog")
	}
	detach(cmd)
	return cmd
}

// captureInstallerOutput sends the macOS installer's output to logPath, as
// msiexec writes its own log on Windows, so a failed update can be diagnosed.
// The caller closes the returned file once the installer has started.
func captureInstallerOutput(cmd *exec.Cmd, logPath string) *os.File {
	if runtime.GOOS == "windows" || logPath == "" {
		return nil
	}
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0640)
	if err != nil {
		logger.Warn("Auto-update: cannot open installer log %s: %v", logPath, err)
		return nil
	}
	cmd.Stdout = f
	cmd.Stderr = f
	return f
}

// installerLogPath returns where the installer's log is written: next to the
// service log, which only administrators can write to.
func installerLogPath() string {
	if p := logger.Path(); p != "" {
		return filepath.Join(filepath.Dir(p), "update-installer.log")
	}
	return ""
}

// removeStaleInstallers deletes installers left by earlier updates.
func removeStaleInstallers(dir string, olderThan time.Duration) {
	matches, _ := filepath.Glob(filepath.Join(dir, installerPrefix+"*"))
	for _, m := range matches {
		info, err := os.Lstat(m)
		if err != nil || !info.Mode().IsRegular() || time.Since(info.ModTime()) < olderThan {
			continue
		}
		os.Remove(m)
	}
}

// isNewer reports whether available is a newer version than current.
// Versions compare by their dotted numeric parts ("v" prefix, pre-release
// and build suffixes ignored), so a device never reinstalls the version it
// runs or moves to an older one. When either version is not numeric, any
// difference counts as newer, as before.
func isNewer(available, current string) bool {
	a, okA := parseVersion(available)
	b, okB := parseVersion(current)
	if !okA || !okB {
		return strings.TrimSpace(available) != "" && available != current
	}
	for i := 0; i < len(a) || i < len(b); i++ {
		var x, y int
		if i < len(a) {
			x = a[i]
		}
		if i < len(b) {
			y = b[i]
		}
		if x != y {
			return x > y
		}
	}
	return false
}

func parseVersion(v string) ([]int, bool) {
	v = strings.TrimSpace(v)
	v = strings.TrimPrefix(strings.TrimPrefix(v, "v"), "V")
	if i := strings.IndexAny(v, "-+ "); i >= 0 {
		v = v[:i]
	}
	if v == "" {
		return nil, false
	}
	parts := strings.Split(v, ".")
	out := make([]int, 0, len(parts))
	for _, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 {
			return nil, false
		}
		out = append(out, n)
	}
	return out, true
}

// verifyInstallerSha256 checks the downloaded installer against the digest
// published by the server. A provided digest must match exactly; the
// comparison is constant-time. When the server publishes no digest (legacy
// publishes predating digest support) the check is skipped with a warning so
// older servers keep working; operators should re-publish versions so every
// rollout carries a digest.
func verifyInstallerSha256(path, expected string) error {
	expected = strings.ToLower(strings.TrimSpace(expected))
	if expected == "" {
		logger.Warn("Auto-update: server did not provide an installer SHA-256 digest; skipping integrity check")
		return nil
	}
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, f); err != nil {
		return err
	}
	actual := hex.EncodeToString(digest.Sum(nil))
	if subtle.ConstantTimeCompare([]byte(actual), []byte(expected)) != 1 {
		return fmt.Errorf("installer sha256 mismatch: got %s want %s", actual, expected)
	}
	return nil
}
