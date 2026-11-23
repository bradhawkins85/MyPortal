package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
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

func TestPortalScanNetworkCommandStartsManualScan(t *testing.T) {
	configFetched := make(chan struct{}, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/tray/config" {
			select {
			case configFetched <- struct{}{}:
			default:
			}
		}
		_ = json.NewEncoder(w).Encode(api.ConfigResponse{NetworkScannerEnabled: false})
	}))
	defer server.Close()
	d := newDaemon(&config.Config{PortalURL: server.URL})
	d.dispatchWSMessage("scan_network", nil)
	select {
	case <-configFetched:
	case <-time.After(5 * time.Second):
		t.Fatal("scan_network from the portal did not start a manual scan")
	}
}

type portCheckReport struct {
	CommandID    int    `json:"command_id"`
	OpenPorts    []int  `json:"open_ports"`
	CheckedCount int    `json:"checked_count"`
	Error        string `json:"error"`
}

func runPortCheck(t *testing.T, cfg api.ConfigResponse, targets []string, msg map[string]json.RawMessage) portCheckReport {
	t.Helper()
	previous := portCheckTargets
	portCheckTargets = func([]string) []string { return targets }
	t.Cleanup(func() { portCheckTargets = previous })
	reports := make(chan portCheckReport, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/tray/config":
			_ = json.NewEncoder(w).Encode(cfg)
		case "/api/tray/network-scan/port-checks/55":
			var report portCheckReport
			_ = json.NewDecoder(r.Body).Decode(&report)
			reports <- report
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	d := newDaemon(&config.Config{PortalURL: server.URL})
	d.handleCheckPorts(msg)
	select {
	case report := <-reports:
		return report
	case <-time.After(5 * time.Second):
		t.Fatal("no port check result was reported")
		return portCheckReport{}
	}
}

func portCheckMessage(ip string, ports ...int) map[string]json.RawMessage {
	raw := func(v any) json.RawMessage { b, _ := json.Marshal(v); return b }
	return map[string]json.RawMessage{
		"command_id": raw(77), "check_id": raw(55), "ip_address": raw(ip), "ports": raw(ports),
	}
}

func TestPortCheckReportsOpenPorts(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			c.Close()
		}
	}()
	open := ln.Addr().(*net.TCPAddr).Port

	report := runPortCheck(t, api.ConfigResponse{NetworkScannerEnabled: true}, []string{"127.0.0.0/8"},
		portCheckMessage("127.0.0.1", open))

	if report.CommandID != 77 || report.Error != "" || report.CheckedCount != 1 ||
		len(report.OpenPorts) != 1 || report.OpenPorts[0] != open {
		t.Fatalf("report = %+v", report)
	}
}

func TestPortCheckRefusedWhenScanningDisabled(t *testing.T) {
	report := runPortCheck(t, api.ConfigResponse{NetworkScannerEnabled: false}, []string{"127.0.0.0/8"},
		portCheckMessage("127.0.0.1", 22))
	if report.Error == "" || len(report.OpenPorts) != 0 {
		t.Fatalf("report = %+v", report)
	}
}

func TestPortCheckRefusedOutsideLocalRanges(t *testing.T) {
	report := runPortCheck(t, api.ConfigResponse{NetworkScannerEnabled: true}, []string{"192.168.1.0/24"},
		portCheckMessage("127.0.0.1", 22))
	if report.Error == "" || len(report.OpenPorts) != 0 {
		t.Fatalf("report = %+v", report)
	}
}

func TestPortCheckRefusesTooManyOrInvalidPorts(t *testing.T) {
	many := make([]int, 1025)
	for i := range many {
		many[i] = i + 1
	}
	for name, ports := range map[string][]int{"too many": many, "out of range": {0, 70000}} {
		report := runPortCheck(t, api.ConfigResponse{NetworkScannerEnabled: true}, []string{"127.0.0.0/8"},
			portCheckMessage("127.0.0.1", ports...))
		if report.Error == "" {
			t.Fatalf("%s: report = %+v", name, report)
		}
	}
}
