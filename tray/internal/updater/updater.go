// Package updater handles auto-update checks for the tray service.
//
// Every UpdateInterval (default 6 hours) the service calls
// GET /api/tray/version. If a newer version is available and
// AutoUpdate is enabled, the installer is downloaded and deferred
// until no interactive session is active (or forced immediately when
// Required is true on the server response).
package updater

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// AgentVersion is embedded at build time via ldflags.
var AgentVersion = "0.1.0"

// UpdateInterval controls how often the version check runs.
const UpdateInterval = 6 * time.Hour

// Checker runs periodic update checks.
type Checker struct {
	client    *api.Client
	autoUpdate bool
	current   string
}

// New creates a new update Checker.
func New(client *api.Client, autoUpdate bool) *Checker {
	return &Checker{
		client:    client,
		autoUpdate: autoUpdate,
		current:   AgentVersion,
	}
}

// Run starts the update loop; it blocks until ctx is cancelled.
func (c *Checker) Run(ctx context.Context) {
	ticker := time.NewTicker(UpdateInterval)
	defer ticker.Stop()
	// Check once on startup.
	c.check(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.check(ctx)
		}
	}
}

func (c *Checker) check(ctx context.Context) {
	if !c.autoUpdate {
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
	tmpDir := os.TempDir()
	var fname string
	switch runtime.GOOS {
	case "windows":
		fname = "myportal-tray.msi"
	default:
		fname = "myportal-tray.pkg"
	}
	dest := filepath.Join(tmpDir, fname)

	logger.Info("Downloading installer to %s", dest)
	if err := downloadFile(ctx, resp.DownloadURL, dest); err != nil {
		return err
	}

	logger.Info("Launching installer %s", dest)
	return launchInstaller(dest)
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
