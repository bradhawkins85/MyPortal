package config_test

import (
	"encoding/json"
	"os"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/config"
)

func TestLoadFromFile(t *testing.T) {
	f, err := os.CreateTemp("", "myportal-tray-config-*.json")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(f.Name())

	cfg := map[string]interface{}{
		"portal_url":  "https://test.example.com",
		"enrol_token": "tok123",
		"auto_update": true,
	}
	if err := json.NewEncoder(f).Encode(cfg); err != nil {
		t.Fatal(err)
	}
	f.Close()

	t.Setenv("MYPORTAL_CONFIG_FILE", f.Name())
	loaded, err := config.Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.PortalURL != "https://test.example.com" {
		t.Errorf("PortalURL: got %s", loaded.PortalURL)
	}
	if loaded.EnrolToken != "tok123" {
		t.Errorf("EnrolToken: got %s", loaded.EnrolToken)
	}
	if !loaded.AutoUpdate {
		t.Error("expected AutoUpdate=true")
	}
}

func TestLoadEnvFallback(t *testing.T) {
	t.Setenv("MYPORTAL_URL", "https://env.example.com")
	t.Setenv("ENROL_TOKEN", "env-token")
	t.Setenv("AUTO_UPDATE", "false")
	// Ensure config file is not set.
	t.Setenv("MYPORTAL_CONFIG_FILE", "")

	// Use a temp file to avoid hitting OS-specific paths.
	f, _ := os.CreateTemp("", "*.json")
	enc := json.NewEncoder(f)
	_ = enc.Encode(map[string]interface{}{
		"portal_url":  "https://env.example.com",
		"enrol_token": "env-token",
		"auto_update": false,
	})
	f.Close()
	defer os.Remove(f.Name())
	t.Setenv("MYPORTAL_CONFIG_FILE", f.Name())

	loaded, err := config.Load()
	if err != nil {
		t.Fatalf("Load: %v", err)
	}
	if loaded.PortalURL != "https://env.example.com" {
		t.Errorf("PortalURL: got %s", loaded.PortalURL)
	}
	if loaded.AutoUpdate {
		t.Error("expected AutoUpdate=false")
	}
}
