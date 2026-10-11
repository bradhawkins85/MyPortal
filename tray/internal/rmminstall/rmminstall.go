// Package rmminstall installs the MyPortal RMM agent (myportal-rmm) when
// MyPortal sends the tray service an install_rmm_agent command.
//
// The tray service runs as LocalSystem/root, so it downloads the agent build
// MyPortal holds for this platform, checks its SHA-256, puts it in a folder
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

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// API is the part of the MyPortal client the installer uses.
type API interface {
	BaseURL() string
	AuthToken() string
	GetRMMAgentPackage(ctx context.Context, goos, goarch string) (*api.RMMAgentPackage, error)
	DownloadRMMAgent(ctx context.Context, downloadPath string, w io.Writer) error
}

// Runner runs the agent binary with args and extra environment and returns
// its combined output.
type Runner func(ctx context.Context, path string, args []string, env []string) ([]byte, error)

// Installer installs the agent into Dir.
type Installer struct {
	API API
	Dir string
	Run Runner
}

// New returns an installer for this platform's install folder.
func New(client API) *Installer {
	return &Installer{API: client, Dir: DefaultDir(), Run: runCommand}
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
	token := in.API.AuthToken()
	if token == "" {
		return "", errors.New("the tray app is not enrolled")
	}
	pkg, err := in.API.GetRMMAgentPackage(ctx, runtime.GOOS, runtime.GOARCH)
	if err != nil {
		return "", fmt.Errorf("find the agent build: %w", err)
	}
	expected := strings.ToLower(strings.TrimSpace(pkg.Sha256))
	if len(expected) != sha256.Size*2 {
		return "", errors.New("MyPortal did not give the agent build's SHA-256")
	}
	if err := os.MkdirAll(in.Dir, 0o755); err != nil {
		return "", err
	}
	// Download next to the final path so the rename stays on one volume.
	tmp, err := os.CreateTemp(in.Dir, "myportal-rmm-*.download")
	if err != nil {
		return "", err
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	digest := sha256.New()
	err = in.API.DownloadRMMAgent(ctx, pkg.DownloadPath, io.MultiWriter(tmp, digest))
	if closeErr := tmp.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return "", fmt.Errorf("download the agent: %w", err)
	}
	actual := hex.EncodeToString(digest.Sum(nil))
	if subtle.ConstantTimeCompare([]byte(actual), []byte(expected)) != 1 {
		return "", fmt.Errorf("agent download SHA-256 mismatch: got %s want %s", actual, expected)
	}
	if err := os.Chmod(tmpPath, 0o755); err != nil {
		return "", err
	}

	final := filepath.Join(in.Dir, binaryName())
	if _, err := os.Stat(final); err == nil {
		// Stop and remove the old service so its binary can be replaced.
		_, _ = in.Run(ctx, final, []string{"uninstall"}, nil)
	}
	if err := os.Rename(tmpPath, final); err != nil {
		return "", fmt.Errorf("put the agent in place: %w", err)
	}
	env := []string{"MYPORTAL_TRAY_TOKEN=" + token}
	if out, err := in.Run(ctx, final, []string{"enrol", "--url", in.API.BaseURL()}, env); err != nil {
		return "", fmt.Errorf("enrol the agent: %w: %s", err, trim(out))
	}
	if out, err := in.Run(ctx, final, []string{"install"}, nil); err != nil {
		return "", fmt.Errorf("install the agent service: %w: %s", err, trim(out))
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
