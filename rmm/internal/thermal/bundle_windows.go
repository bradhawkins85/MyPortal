//go:build windows

package thermal

import (
	"bytes"
	"crypto/sha256"
	"embed"
	"fmt"
	"os"
	"path/filepath"
	"sync"

	"github.com/bradhawkins85/myportal-rmm/internal/state"
)

// Production Windows builds populate this folder with the pinned NuGet library,
// dependencies and notices before compiling. The files travel inside the agent
// executable, covered by its existing verified download and rollback path.
//
//go:embed bundle/*
var bundle embed.FS

var bundleMu sync.Mutex

func bundlePath() (string, error) {
	// Startup setup and the first check-in may extract the bundle together.
	bundleMu.Lock()
	defer bundleMu.Unlock()
	entries, err := bundle.ReadDir("bundle")
	if err != nil {
		return "", err
	}
	digest := sha256.New()
	for _, entry := range entries {
		data, err := bundle.ReadFile("bundle/" + entry.Name())
		if err != nil {
			return "", err
		}
		digest.Write([]byte(entry.Name()))
		digest.Write(data)
	}
	root := filepath.Join(state.Dir(), "LibreHardwareMonitor")
	destination := filepath.Join(root, fmt.Sprintf("%x", digest.Sum(nil)))
	dll := filepath.Join(destination, "LibreHardwareMonitorLib.dll")
	if _, err := os.Stat(dll); err == nil {
		for _, entry := range entries {
			expected, err := bundle.ReadFile("bundle/" + entry.Name())
			if err != nil {
				return "", err
			}
			actual, err := os.ReadFile(filepath.Join(destination, entry.Name()))
			if err != nil || !bytes.Equal(actual, expected) {
				return "", fmt.Errorf("thermal bundle file failed verification: %s", entry.Name())
			}
		}
		return dll, nil
	}
	if _, err := bundle.ReadFile("bundle/LibreHardwareMonitorLib.dll"); err != nil {
		return "", fmt.Errorf("Windows build has no thermal bundle: %w", err)
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return "", err
	}
	staging, err := os.MkdirTemp(root, "bundle-")
	if err != nil {
		return "", err
	}
	defer os.RemoveAll(staging)
	for _, entry := range entries {
		data, err := bundle.ReadFile("bundle/" + entry.Name())
		if err != nil {
			return "", err
		}
		if err := os.WriteFile(filepath.Join(staging, entry.Name()), data, 0600); err != nil {
			return "", err
		}
	}
	if err := os.Rename(staging, destination); err != nil {
		return "", err
	}
	return dll, nil
}
