package config

import (
	"encoding/json"
	"os"
	"runtime"
	"strings"
)

// Config holds the per-install configuration for the tray application.
// On Windows it is read from the registry key
// HKLM\Software\MyPortal\Tray; on macOS from
// /Library/Preferences/io.myportal.tray.env; for development, the
// CONFIG_FILE env var can point at a JSON file.
type Config struct {
	PortalURL  string `json:"portal_url"`
	EnrolToken string `json:"enrol_token"`
	// AutoUpdate can be set to "false" in the env file to suppress
	// automatic installer downloads (useful for RMM-managed fleets).
	AutoUpdate bool `json:"auto_update"`
}

// Load reads the configuration from the platform-appropriate location.
func Load() (*Config, error) {
	// Dev override
	if f := os.Getenv("MYPORTAL_CONFIG_FILE"); f != "" {
		return loadFile(f)
	}

	switch runtime.GOOS {
	case "windows":
		return loadWindows()
	case "darwin":
		return loadMacOS()
	default:
		return loadEnvFallback(), nil
	}
}

func loadFile(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return nil, err
	}
	if cfg.AutoUpdate == false && os.Getenv("AUTO_UPDATE") == "true" {
		cfg.AutoUpdate = true
	}
	return &cfg, nil
}

// loadEnvFallback reads from environment variables directly —
// used in CI / container environments.
func loadEnvFallback() *Config {
	au := true
	if strings.ToLower(os.Getenv("AUTO_UPDATE")) == "false" {
		au = false
	}
	return &Config{
		PortalURL:  os.Getenv("MYPORTAL_URL"),
		EnrolToken: os.Getenv("ENROL_TOKEN"),
		AutoUpdate: au,
	}
}
