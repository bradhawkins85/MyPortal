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
	"runtime"
	"strings"
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
	resp, err := c.client.GetVersion(ctx)
	if err != nil {
		logger.Warn("Auto-update check failed: %v", err)
		return
	}
	if resp.Version == c.current {
		return
	}
	logger.Info("New tray version available: %s (current %s)", resp.Version, c.current)
	if resp.DownloadURL == "" {
		return
	}
	if err := c.downloadAndInstall(ctx, resp); err != nil {
		logger.Error("Auto-update failed: %v", err)
	}
}

func (c *Checker) downloadAndInstall(ctx context.Context, resp *api.VersionResponse) error {
	var ext string
	switch runtime.GOOS {
	case "windows":
		ext = ".msi"
	default:
		ext = ".pkg"
	}
	// Use a randomly named temp file so a local attacker cannot pre-create
	// or symlink the fixed installer path the service will execute.
	dest, err := os.CreateTemp(os.TempDir(), "myportal-tray-*"+ext)
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
	defer os.Remove(tmpPath)

	if err := verifyInstallerSha256(tmpPath, resp.Sha256); err != nil {
		return err
	}

	logger.Info("Launching installer %s", tmpPath)
	return launchInstaller(tmpPath)
}

func launchInstaller(path string) error {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("msiexec.exe", "/i", path, "/qn", "/norestart")
	default:
		cmd = exec.Command("installer", "-pkg", path, "-target", "/")
	}
	return cmd.Start() // fire and forget — service will restart from installer
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
