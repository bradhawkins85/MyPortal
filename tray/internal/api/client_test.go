package api_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// newStubServer returns a minimal stub of the MyPortal tray API.
func newStubServer(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()

	mux.HandleFunc("/api/tray/enrol", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		resp := api.EnrolResponse{
			DeviceUID:           "test-device-uid",
			AuthToken:           "test-auth-token",
			PollIntervalSeconds: 30,
		}
		_ = json.NewEncoder(w).Encode(resp)
	})

	mux.HandleFunc("/api/tray/config", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-auth-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		resp := api.ConfigResponse{
			Version: 1,
			Menu: []api.MenuNode{
				{Type: "label", Label: "MyPortal"},
				{Type: "separator"},
				{Type: "open_chat", Label: "Chat with helpdesk"},
			},
			ChatEnabled: true,
		}
		_ = json.NewEncoder(w).Encode(resp)
	})

	mux.HandleFunc("/api/tray/heartbeat", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-auth-token" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.WriteHeader(http.StatusOK)
	})

	mux.HandleFunc("/api/tray/version", func(w http.ResponseWriter, r *http.Request) {
		resp := api.VersionResponse{
			Version:  "0.1.0",
			Required: false,
		}
		_ = json.NewEncoder(w).Encode(resp)
	})

	return httptest.NewServer(mux)
}

func TestEnrol(t *testing.T) {
	srv := newStubServer(t)
	defer srv.Close()

	client := api.New(srv.URL)
	resp, err := client.Enrol(context.Background(), api.EnrolRequest{
		InstallToken: "test-install-token",
		OS:           "linux",
		Hostname:     "test-host",
		AgentVersion: "0.1.0",
	})
	if err != nil {
		t.Fatalf("Enrol: %v", err)
	}
	if resp.DeviceUID != "test-device-uid" {
		t.Errorf("expected device_uid=test-device-uid, got %s", resp.DeviceUID)
	}
	if resp.AuthToken != "test-auth-token" {
		t.Errorf("expected auth_token=test-auth-token, got %s", resp.AuthToken)
	}
}

func TestGetConfig(t *testing.T) {
	srv := newStubServer(t)
	defer srv.Close()

	client := api.New(srv.URL)
	client.SetAuth("test-device-uid", "test-auth-token")

	cfg, err := client.GetConfig(context.Background())
	if err != nil {
		t.Fatalf("GetConfig: %v", err)
	}
	if cfg.Version != 1 {
		t.Errorf("expected version=1, got %d", cfg.Version)
	}
	if len(cfg.Menu) != 3 {
		t.Errorf("expected 3 menu nodes, got %d", len(cfg.Menu))
	}
	if !cfg.ChatEnabled {
		t.Error("expected chat_enabled=true")
	}
}

func TestGetConfigUnauthorized(t *testing.T) {
	srv := newStubServer(t)
	defer srv.Close()

	client := api.New(srv.URL)
	// No auth set — should get 401.
	_, err := client.GetConfig(context.Background())
	if err == nil {
		t.Error("expected error for unauthorized request")
	}
}

func TestHeartbeat(t *testing.T) {
	srv := newStubServer(t)
	defer srv.Close()

	client := api.New(srv.URL)
	client.SetAuth("test-device-uid", "test-auth-token")

	if err := client.Heartbeat(context.Background(), api.HeartbeatRequest{
		ConsoleUser:  "testuser",
		AgentVersion: "0.1.0",
	}); err != nil {
		t.Fatalf("Heartbeat: %v", err)
	}
}

func TestGetVersion(t *testing.T) {
	srv := newStubServer(t)
	defer srv.Close()

	client := api.New(srv.URL)
	ver, err := client.GetVersion(context.Background())
	if err != nil {
		t.Fatalf("GetVersion: %v", err)
	}
	if ver.Version == "" {
		t.Error("expected non-empty version")
	}
}

func TestEnrolThenConfigRoundTrip(t *testing.T) {
	srv := newStubServer(t)
	defer srv.Close()

	client := api.New(srv.URL)

	// Step 1: enrol.
	enrolResp, err := client.Enrol(context.Background(), api.EnrolRequest{
		InstallToken: "test-install-token",
		OS:           "windows",
		Hostname:     "DESKTOP-TEST",
		AgentVersion: "0.1.0",
	})
	if err != nil {
		t.Fatalf("Enrol: %v", err)
	}
	if enrolResp.DeviceUID == "" {
		t.Fatal("empty device_uid")
	}

	// Step 2: get config using the returned auth token.
	cfg, err := client.GetConfig(context.Background())
	if err != nil {
		t.Fatalf("GetConfig after enrol: %v", err)
	}
	if cfg.Version < 0 {
		t.Error("negative version")
	}

	// Step 3: heartbeat.
	if err := client.Heartbeat(context.Background(), api.HeartbeatRequest{
		AgentVersion: "0.1.0",
	}); err != nil {
		t.Fatalf("Heartbeat after enrol: %v", err)
	}
}
