// Package rmminstall installs the MyPortal RMM agent (myportal-rmm) when
// MyPortal sends the tray service an install_rmm_agent command.
//
// The tray service runs as LocalSystem/root, so it downloads the agent build
// from GitHub (falling back to MyPortal), checks its SHA-256, puts it in a folder
// only administrators can write to, enrols it with the tray device's own token
// (passed in the environment, never on the command line) and installs its
// service:
//
//   - Windows: %ProgramFiles%\MyPortal RMM\myportal-rmm.exe
//   - macOS:   /Library/Application Support/MyPortal RMM/myportal-rmm
package rmminstall

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/kardianos/service"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// API is the part of the MyPortal client the installer uses.
type API interface {
	BaseURL() string
	AuthToken() string
	GetRMMAgentPackage(ctx context.Context, goos, goarch string, version ...string) (*api.RMMAgentPackage, error)
	DownloadRMMAgent(ctx context.Context, downloadPath string, w io.Writer) error
}

// PackageSource is the lookup and download interface shared by both sources.
type PackageSource interface {
	GetRMMAgentPackage(ctx context.Context, goos, goarch string, version ...string) (*api.RMMAgentPackage, error)
	DownloadRMMAgent(ctx context.Context, downloadPath string, w io.Writer) error
}

// Runner runs the agent binary with args and extra environment and returns
// its combined output.
type Runner func(ctx context.Context, path string, args []string, env []string) ([]byte, error)

// Installer installs the agent into Dir.
type Installer struct {
	API    API
	GitHub PackageSource
	Dir    string
	// StateFile identifies a previously enrolled agent when its binary is missing.
	StateFile string
	Run       Runner
	// ServiceStatus checks the actual service independently of binary version.
	ServiceStatus func() (service.Status, error)
	// WaitServiceRemoved waits for asynchronous Windows service deletion.
	WaitServiceRemoved func(context.Context) error
}

// New returns an installer for this platform's install folder.
func New(client API) *Installer {
	return &Installer{API: client, GitHub: newGitHubSource(), Dir: DefaultDir(), StateFile: defaultStateFile(), Run: runCommand,
		ServiceStatus: rmmServiceStatus, WaitServiceRemoved: waitServiceRemoved}
}

// DefaultDir is where the agent binary lives on this platform.
func DefaultDir() string {
	if runtime.GOOS == "windows" {
		base := os.Getenv("ProgramFiles")
		if base == "" {
			base = `C:\Program Files`
		}
		return filepath.Join(base, "MyPortal RMM")
	}
	return "/Library/Application Support/MyPortal RMM"
}

func defaultStateFile() string {
	if runtime.GOOS == "windows" {
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "MyPortal RMM", "rmm-state.json")
	}
	return filepath.Join(DefaultDir(), "rmm-state.json")
}

func binaryName() string {
	if runtime.GOOS == "windows" {
		return "myportal-rmm.exe"
	}
	return "myportal-rmm"
}

func runCommand(ctx context.Context, path string, args []string, env []string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, path, args...)
	cmd.Env = append(os.Environ(), env...)
	return cmd.CombinedOutput()
}

// Install downloads, enrols and installs the agent. It returns the installed
// version.
func (in *Installer) Install(ctx context.Context) (string, error) {
	return in.InstallVersion(ctx, "")
}

// EnsureVersion upgrades an existing agent to the tray version. Missing agents
// without saved enrolment are left to the portal's deployment policy.
func (in *Installer) EnsureVersion(ctx context.Context, version string) (bool, error) {
	final := filepath.Join(in.Dir, binaryName())
	if _, err := os.Stat(final); errors.Is(err, os.ErrNotExist) {
		// Restore previously enrolled agents without enabling deployment on
		// devices that have never had an RMM agent.
		if in.StateFile == "" {
			return false, nil
		}
		if _, stateErr := os.Stat(in.StateFile); errors.Is(stateErr, os.ErrNotExist) {
			return false, nil
		} else if stateErr != nil {
			return false, stateErr
		}
		if strings.TrimSpace(version) == "" || strings.Contains(version, "dev") {
			return false, errors.New("the tray has no release version to match")
		}
		_, repairErr := in.RepairVersion(ctx, version)
		return repairErr == nil, repairErr
	} else if err != nil {
		return false, err
	}
	probeCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	out, err := in.Run(probeCtx, final, []string{"version"}, nil)
	cancel()
	if err == nil && sameVersion(string(out), version) {
		return in.ensureService(ctx, final)
	}
	if strings.TrimSpace(version) == "" || strings.Contains(version, "dev") {
		return false, errors.New("the tray has no release version to match")
	}
	if err != nil {
		_, err = in.RepairVersion(ctx, version)
	} else {
		_, err = in.InstallVersion(ctx, version)
	}
	return err == nil, err
}

func sameVersion(a, b string) bool {
	return strings.TrimPrefix(strings.TrimSpace(a), "v") == strings.TrimPrefix(strings.TrimSpace(b), "v")
}

// InstallVersion installs the matching release, refusing a different package.
// An empty version preserves compatibility with older deployment callers.
func (in *Installer) InstallVersion(ctx context.Context, version string) (string, error) {
	return in.installVersion(ctx, version, false)
}

// RepairVersion reinstalls even a matching agent and refreshes its enrolment.
// Success requires a running service and an authenticated portal check-in.
func (in *Installer) RepairVersion(ctx context.Context, version string) (string, error) {
	return in.installVersion(ctx, version, true)
}

func (in *Installer) installVersion(ctx context.Context, version string, repair bool) (installedVersion string, installErr error) {
	token := in.API.AuthToken()
	if token == "" {
		return "", errors.New("the tray app is not enrolled")
	}
	if err := os.MkdirAll(in.Dir, 0o755); err != nil {
		return "", err
	}
	var pkg *api.RMMAgentPackage
	var tmpPath string
	var err, githubErr error
	if in.GitHub != nil {
		// Reserve time for the portal fallback within the service's 15 minute limit.
		githubCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
		pkg, tmpPath, githubErr = in.downloadPackage(githubCtx, in.GitHub, version)
		cancel()
		if githubErr != nil {
			if ctx.Err() != nil {
				return "", ctx.Err()
			}
			logger.Warn("RMM GitHub download failed: %v; trying MyPortal", githubErr)
		}
	}
	if tmpPath == "" {
		pkg, tmpPath, err = in.downloadPackage(ctx, in.API, version)
		if err != nil {
			if githubErr != nil {
				return "", errors.Join(fmt.Errorf("GitHub: %w", githubErr), fmt.Errorf("MyPortal: %w", err))
			}
			return "", err
		}
	}
	defer os.Remove(tmpPath)
	if err := os.Chmod(tmpPath, 0o755); err != nil {
		return "", err
	}

	final := filepath.Join(in.Dir, binaryName())
	backup := ""
	hadAgent := false
	if _, err := os.Stat(final); err == nil {
		hadAgent = true
		file, err := os.CreateTemp(in.Dir, "myportal-rmm-*.previous")
		if err != nil {
			return "", fmt.Errorf("create agent backup: %w", err)
		}
		backup = file.Name()
		if err := file.Close(); err != nil {
			_ = os.Remove(backup)
			return "", err
		}
		if err := os.Remove(backup); err != nil {
			return "", err
		}
		out, uninstallErr := in.uninstall(ctx, final)
		if uninstallErr != nil {
			// A damaged old executable cannot remove its service. The verified
			// replacement uses the same service name and can remove it instead.
			out, uninstallErr = in.uninstall(ctx, tmpPath)
		}
		if uninstallErr != nil {
			// uninstall may have stopped the service before failing.
			return "", errors.Join(fmt.Errorf("remove the old agent service: %w: %s", uninstallErr, trim(out)), in.restartAgent(final))
		}
		if err := os.Rename(final, backup); err != nil {
			return "", errors.Join(fmt.Errorf("back up the agent: %w", err), in.restartAgent(final))
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return "", err
	} else if repair {
		// A deleted binary can leave its old service registered (or even
		// running). Remove that registration using the verified replacement.
		if out, err := in.uninstall(ctx, tmpPath); err != nil {
			return "", fmt.Errorf("remove the orphaned agent service: %w: %s", err, trim(out))
		}
	}
	// Keep enrolment/state on upgrades. If replacement or startup fails, put
	// the old binary back and restart it using an independent recovery timeout.
	success := false
	defer func() {
		if repair && !hadAgent && !success {
			// Do not leave a failed first repair looking like a matching install:
			// the version loop must be able to retry restoring a missing binary.
			recoveryCtx, cancel := context.WithTimeout(context.Background(), time.Minute)
			defer cancel()
			if _, err := os.Stat(final); err == nil {
				_, _ = in.uninstall(recoveryCtx, final)
				if err := os.Remove(final); err != nil {
					installErr = errors.Join(installErr, fmt.Errorf("remove failed repair: %w", err))
				}
			}
		}
		if hadAgent && !success {
			recoveryCtx, cancel := context.WithTimeout(context.Background(), time.Minute)
			defer cancel()
			if _, err := os.Stat(final); err == nil {
				_, _ = in.uninstall(recoveryCtx, final)
			}
			if err := os.Remove(final); err != nil && !errors.Is(err, os.ErrNotExist) {
				installErr = errors.Join(installErr, fmt.Errorf("restore old agent: %w (backup at %s)", err, backup))
				return
			}
			if err := os.Rename(backup, final); err != nil {
				installErr = errors.Join(installErr, fmt.Errorf("restore old agent: %w (backup at %s)", err, backup))
				return
			}
			if out, err := in.Run(recoveryCtx, final, []string{"install"}, nil); err != nil {
				installErr = errors.Join(installErr, fmt.Errorf("restart old agent: %w: %s", err, trim(out)))
			}
		}
	}()
	if err := os.Rename(tmpPath, final); err != nil {
		return "", fmt.Errorf("put the agent in place: %w", err)
	}
	if !hadAgent || repair {
		env := []string{"MYPORTAL_TRAY_TOKEN=" + token}
		if out, err := in.Run(ctx, final, []string{"enrol", "--url", in.API.BaseURL()}, env); err != nil {
			return "", fmt.Errorf("enrol the agent: %w: %s", err, trim(out))
		}
	}
	if out, err := in.Run(ctx, final, []string{"install"}, nil); err != nil {
		return "", fmt.Errorf("install the agent service: %w: %s", err, trim(out))
	}
	if err := in.waitRunning(ctx); err != nil {
		return "", err
	}
	if repair {
		verifyCtx, cancel := context.WithTimeout(ctx, time.Minute)
		out, err := in.Run(verifyCtx, final, []string{"verify"}, nil)
		cancel()
		if err != nil {
			return "", fmt.Errorf("verify the agent connection: %w: %s", err, trim(out))
		}
	}
	success = true
	if hadAgent {
		_ = os.Remove(backup)
	}
	return pkg.Version, nil
}

func trim(out []byte) string {
	out = bytes.TrimSpace(out)
	if len(out) > 1500 {
		out = out[len(out)-1500:]
	}
	return string(out)
}

// downloadPackage verifies the complete download before any service is stopped.
func (in *Installer) downloadPackage(ctx context.Context, source PackageSource, version string) (*api.RMMAgentPackage, string, error) {
	pkg, err := source.GetRMMAgentPackage(ctx, runtime.GOOS, runtime.GOARCH, version)
	if err != nil {
		return nil, "", fmt.Errorf("find the agent build: %w", err)
	}
	if pkg == nil {
		return nil, "", errors.New("missing agent package")
	}
	if version != "" && !sameVersion(pkg.Version, version) {
		return nil, "", fmt.Errorf("RMM package version %q does not match tray version %q", pkg.Version, version)
	}
	expected := strings.ToLower(strings.TrimSpace(pkg.Sha256))
	decoded, err := hex.DecodeString(expected)
	if err != nil || len(decoded) != sha256.Size {
		return nil, "", errors.New("missing or invalid agent build SHA-256")
	}
	// Download next to the final path so the rename stays on one volume.
	tmp, err := os.CreateTemp(in.Dir, "myportal-rmm-*.download")
	if err != nil {
		return nil, "", err
	}
	tmpPath := tmp.Name()
	success := false
	defer func() {
		if !success {
			_ = os.Remove(tmpPath)
		}
	}()
	digest := sha256.New()
	err = source.DownloadRMMAgent(ctx, pkg.DownloadPath, io.MultiWriter(tmp, digest))
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return nil, "", fmt.Errorf("download the agent: %w", err)
	}
	actual := hex.EncodeToString(digest.Sum(nil))
	if subtle.ConstantTimeCompare([]byte(actual), []byte(expected)) != 1 {
		return nil, "", fmt.Errorf("agent download SHA-256 mismatch: got %s want %s", actual, expected)
	}
	success = true
	return pkg, tmpPath, nil
}
