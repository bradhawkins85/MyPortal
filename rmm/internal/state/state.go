// Package state stores the RMM agent's enrolment between restarts.
//
// The file lives in a directory only the service account (and, on Windows,
// Administrators) can read or write, because it holds the agent's bearer
// token and the address of the portal the agent takes jobs from. On Windows
// that is enforced with an explicit access list (see internal/secdir):
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

	"github.com/bradhawkins85/myportal-rmm/internal/secdir"
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

// Prepare locks the state folder down and removes anything in it that the
// service did not create (links, or files owned by another user). Call it
// once at start-up before Load. It returns the paths it removed.
func Prepare() ([]string, error) {
	if err := secdir.Secure(Dir(), secdir.Private); err != nil {
		return nil, err
	}
	return secdir.Sweep(Dir()), nil
}

// Save writes the state with owner-only permissions.
func Save(s State) error {
	if err := secdir.Secure(Dir(), secdir.Private); err != nil {
		return err
	}
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	tmp := Path() + ".tmp"
	// Never write into a temporary file someone else left behind.
	if err := os.Remove(tmp); err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, Path())
}
