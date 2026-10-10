// Package state stores the RMM agent's enrolment between restarts.
//
// The file lives in a directory only the service account can read, because
// it holds the agent's bearer token:
//
//   - Windows: %ProgramData%\MyPortal RMM\rmm-state.json
//   - macOS:   /Library/Application Support/MyPortal RMM/rmm-state.json
//   - Linux:   /var/lib/myportal-rmm/rmm-state.json
package state

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
)

const fileName = "rmm-state.json"

// State is what the agent needs to talk to MyPortal.
type State struct {
	PortalURL string `json:"portal_url"`
	AgentUID  string `json:"agent_uid"`
	AuthToken string `json:"auth_token"`
}

// Dir returns the agent's data directory for this platform.
func Dir() string {
	if override := os.Getenv("MYPORTAL_RMM_STATE_DIR"); override != "" {
		return override
	}
	switch runtime.GOOS {
	case "windows":
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "MyPortal RMM")
	case "darwin":
		return "/Library/Application Support/MyPortal RMM"
	default:
		return "/var/lib/myportal-rmm"
	}
}

// Path returns the full path of the state file.
func Path() string {
	return filepath.Join(Dir(), fileName)
}

// ErrNotEnrolled is returned by Load when the agent has never enrolled.
var ErrNotEnrolled = errors.New("the RMM agent is not enrolled")

// Load reads the saved state.
func Load() (State, error) {
	var s State
	data, err := os.ReadFile(Path())
	if errors.Is(err, os.ErrNotExist) {
		return s, ErrNotEnrolled
	}
	if err != nil {
		return s, err
	}
	if err := json.Unmarshal(data, &s); err != nil {
		return s, err
	}
	if s.PortalURL == "" || s.AuthToken == "" {
		return s, ErrNotEnrolled
	}
	return s, nil
}

// Save writes the state with owner-only permissions.
func Save(s State) error {
	if err := os.MkdirAll(Dir(), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	tmp := Path() + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, Path())
}
