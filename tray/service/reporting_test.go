package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/config"
)

func isolateReporting(t *testing.T) {
	previousDir, previousCollect := stateDir, collectDefenderDetections
	path := t.TempDir()
	stateDir = func() string { return path }
	t.Cleanup(func() { stateDir, collectDefenderDetections = previousDir, previousCollect })
}

func TestRoutineReportsStayWithinOneToTwoHours(t *testing.T) {
	for range 1000 {
		if interval := routineInterval(); interval < time.Hour || interval >= 2*time.Hour {
			t.Fatalf("unexpected interval %s", interval)
		}
	}
}

func TestScannerUsesCachedConfigWithoutPollingPortal(t *testing.T) {
	isolateReporting(t)
	requests := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		_ = json.NewEncoder(w).Encode(api.ConfigResponse{Version: 2})
	}))
	defer server.Close()
	d := newDaemon(&config.Config{PortalURL: server.URL})
	d.refreshConfig()
	if d.currentConfig() == nil || d.currentConfig().Version != 2 {
		t.Fatal("config not cached")
	}
	close(d.stopCh)
	d.networkScannerLoop()
	if requests != 1 {
		t.Fatalf("scanner made redundant requests: %d", requests)
	}
}

func TestDetectionsRetryFailuresAndOnlyUploadChanges(t *testing.T) {
	isolateReporting(t)
	detections := []api.DefenderDetection{{DetectionUID: "a", Status: "active"}, {DetectionUID: "b", Status: "active"}}
	collectDefenderDetections = func(ctx context.Context) ([]api.DefenderDetection, error) { return detections, nil }
	var mu sync.Mutex
	posts := []string{}
	failed := false
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/tray/defender/policy" {
			_ = json.NewEncoder(w).Encode(api.DefenderPolicy{Enabled: true})
			return
		}
		var detection api.DefenderDetection
		if err := json.NewDecoder(r.Body).Decode(&detection); err != nil {
			t.Error(err)
		}
		mu.Lock()
		defer mu.Unlock()
		posts = append(posts, detection.DetectionUID)
		if detection.DetectionUID == "b" && !failed {
			failed = true
			w.WriteHeader(503)
		}
	}))
	defer server.Close()
	d := newDaemon(&config.Config{PortalURL: server.URL})
	if err := d.reportDefenderDetections(context.Background()); err == nil {
		t.Fatal("failed upload was lost")
	}
	if err := d.reportDefenderDetections(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := d.reportDefenderDetections(context.Background()); err != nil {
		t.Fatal(err)
	}
	detections[0].Status = "remediated"
	if err := d.reportDefenderDetections(context.Background()); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	defer mu.Unlock()
	if fmt.Sprint(posts) != "[a b b a]" {
		t.Fatalf("unexpected retries or duplicates: %v", posts)
	}
}

func TestExcludedDevicesDoNotCollectDetections(t *testing.T) {
	isolateReporting(t)
	collectDefenderDetections = func(ctx context.Context) ([]api.DefenderDetection, error) {
		t.Fatal("excluded device collected Defender data")
		return nil, nil
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(api.DefenderPolicy{Enabled: false})
	}))
	defer server.Close()
	d := newDaemon(&config.Config{PortalURL: server.URL})
	if err := d.reportDefenderDetections(context.Background()); err != nil {
		t.Fatal(err)
	}
}

func TestDefenderCommandsWakeImmediatelyWithoutRoutineTimer(t *testing.T) {
	d := newDaemon(&config.Config{PortalURL: "https://example.invalid"})
	d.dispatchWSMessage("defender_commands", nil)
	select {
	case <-d.defenderCommands:
	default:
		t.Fatal("command wakeup lost")
	}
}
