// myportal-tray-service is the privileged background daemon for the
// MyPortal Tray App.
//
// # Responsibilities
//
//   - Enrols the device with the MyPortal server using the install token
//     stored in the registry (Windows) or plist (macOS).
//   - Maintains a persistent WebSocket connection to /ws/tray/{device_uid}
//     with exponential back-off and jitter.
//   - Sends a heartbeat every 30 s.
//   - Pulls /api/tray/config and caches it to disk; re-fetches on
//     config_changed WebSocket event.
//   - Dispatches server commands (chat_open, show_notification, config_changed)
//     to the UI agent over the local IPC socket.
//   - Runs the auto-update checker every 6 hours.
//
// # Platforms
//
//   - Windows: installed as a Windows Service running as LocalSystem.
//   - macOS:   installed as a LaunchDaemon running as root.
//
// Both use github.com/kardianos/service for the lifecycle abstraction.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	"math/rand"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
	"github.com/kardianos/service"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/config"
	"github.com/bradhawkins85/myportal-tray/internal/ipc"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"github.com/bradhawkins85/myportal-tray/internal/notify"
	"github.com/bradhawkins85/myportal-tray/internal/updater"
)

const (
	heartbeatInterval = 30 * time.Second
	configCacheName   = "tray-config.json"
	stateFileName     = "tray-state.json"
)

// -----------------------------------------------------------------
// Persistent state (auth token between restarts)
// -----------------------------------------------------------------

type persistedState struct {
	DeviceUID string `json:"device_uid"`
	AuthToken string `json:"auth_token"`
}

func stateDir() string {
	switch runtime.GOOS {
	case "windows":
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "MyPortal", "tray")
	default:
		return "/Library/Application Support/MyPortal/Tray"
	}
}

func loadState() *persistedState {
	path := filepath.Join(stateDir(), stateFileName)
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var s persistedState
	if err := json.Unmarshal(data, &s); err != nil {
		return nil
	}
	return &s
}

func saveState(s persistedState) {
	dir := stateDir()
	_ = os.MkdirAll(dir, 0700)
	data, _ := json.Marshal(s)
	_ = os.WriteFile(filepath.Join(dir, stateFileName), data, 0600)
}

// -----------------------------------------------------------------
// Daemon
// -----------------------------------------------------------------

type daemon struct {
	cfg     *config.Config
	client  *api.Client
	ipcSrv  *ipc.Server
	stopCh  chan struct{}
}

func newDaemon(cfg *config.Config) *daemon {
	return &daemon{
		cfg:    cfg,
		client: api.New(cfg.PortalURL),
		stopCh: make(chan struct{}),
	}
}

func (d *daemon) Start(s service.Service) error {
	go d.run()
	return nil
}

func (d *daemon) Stop(s service.Service) error {
	close(d.stopCh)
	return nil
}

func (d *daemon) run() {
	if err := logger.Init("service"); err != nil {
		logger.Error("logger init: %v", err)
	}
	logger.Info("MyPortal Tray Service starting (version %s)", updater.AgentVersion)

	// Start IPC server for the UI agent.
	var err error
	d.ipcSrv, err = ipc.NewServer()
	if err != nil {
		logger.Error("IPC server: %v — chat delivery disabled", err)
	}

	// Enrol (or restore persisted state).
	if err := d.ensureEnrolled(); err != nil {
		logger.Error("Enrolment failed: %v", err)
	}

	// Auto-update checker.
	updateCtx, cancelUpdate := context.WithCancel(context.Background())
	defer cancelUpdate()
	checker := updater.New(d.client, d.cfg.AutoUpdate)
	go checker.Run(updateCtx)

	// Main WS + heartbeat loop.
	go d.wsLoop()
	go d.heartbeatLoop()

	<-d.stopCh
	cancelUpdate()
	if d.ipcSrv != nil {
		d.ipcSrv.Close()
	}
	logger.Info("MyPortal Tray Service stopped")
}

func (d *daemon) ensureEnrolled() error {
	if s := loadState(); s != nil && s.AuthToken != "" {
		d.client.SetAuth(s.DeviceUID, s.AuthToken)
		logger.Info("Restored persisted auth (device_uid=%s)", s.DeviceUID)
		return nil
	}

	if d.cfg.EnrolToken == "" {
		return fmt.Errorf("no enrol token configured")
	}

	facts := collectFacts()
	resp, err := d.client.Enrol(context.Background(), api.EnrolRequest{
		InstallToken: d.cfg.EnrolToken,
		OS:           facts.OS,
		OSVersion:    facts.OSVersion,
		Hostname:     facts.Hostname,
		AgentVersion: updater.AgentVersion,
	})
	if err != nil {
		return err
	}
	saveState(persistedState{DeviceUID: resp.DeviceUID, AuthToken: resp.AuthToken})
	logger.Info("Enrolled: device_uid=%s", resp.DeviceUID)
	return nil
}

// wsLoop keeps the WebSocket alive with exponential back-off.
func (d *daemon) wsLoop() {
	attempt := 0
	for {
		select {
		case <-d.stopCh:
			return
		default:
		}

		ctx, cancel := context.WithCancel(context.Background())
		conn, err := d.client.ConnectWS(ctx)
		if err != nil {
			cancel()
			delay := backoff(attempt)
			logger.Warn("WS connect failed (attempt %d): %v — retry in %v", attempt, err, delay)
			attempt++
			sleep(d.stopCh, delay)
			continue
		}
		attempt = 0
		logger.Info("WS connected")
		d.handleWS(ctx, conn)
		cancel()
		conn.Close()

		select {
		case <-d.stopCh:
			return
		case <-time.After(2 * time.Second):
		}
	}
}

func (d *daemon) handleWS(ctx context.Context, conn *websocket.Conn) {
	// Ping goroutine.
	go func() {
		ticker := time.NewTicker(20 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := conn.WriteJSON(map[string]string{"type": "pong"}); err != nil {
					return
				}
			}
		}
	}()

	for {
		var msg map[string]json.RawMessage
		if err := conn.ReadJSON(&msg); err != nil {
			if !websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				logger.Warn("WS read: %v", err)
			}
			return
		}
		msgType := ""
		if t, ok := msg["type"]; ok {
			_ = json.Unmarshal(t, &msgType)
		}
		d.dispatchWSMessage(msgType, msg)
	}
}

func (d *daemon) dispatchWSMessage(msgType string, msg map[string]json.RawMessage) {
	switch msgType {
	case "ping":
		// nothing — pong is sent in the goroutine above
	case "config_changed":
		logger.Info("config_changed received — re-fetching config")
		go d.refreshConfig()
		if d.ipcSrv != nil {
			d.ipcSrv.Broadcast(ipc.Message{Type: "config_changed"})
		}
	case "chat_open":
		logger.Info("chat_open received")
		if d.ipcSrv != nil {
			rawPayload, _ := json.Marshal(msg)
			d.ipcSrv.Broadcast(ipc.Message{
				Type:    "chat_open",
				Payload: json.RawMessage(rawPayload),
			})
		}
	case "show_notification":
		if d.ipcSrv != nil {
			payload := msg["payload"]
			var n notify.Notification
			if err := json.Unmarshal(payload, &n); err == nil {
				notify.Send(d.ipcSrv, n)
			}
		}
	}
}

func (d *daemon) heartbeatLoop() {
	ticker := time.NewTicker(heartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case <-d.stopCh:
			return
		case <-ticker.C:
			facts := collectFacts()
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			if err := d.client.Heartbeat(ctx, api.HeartbeatRequest{
				ConsoleUser:  facts.ConsoleUser,
				AgentVersion: updater.AgentVersion,
			}); err != nil {
				logger.Warn("Heartbeat: %v", err)
			}
			cancel()
		}
	}
}

func (d *daemon) refreshConfig() {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	cfg, err := d.client.GetConfig(ctx)
	if err != nil {
		logger.Warn("GetConfig: %v", err)
		return
	}
	// Persist config to disk for the UI agent to read.
	data, _ := json.Marshal(cfg)
	dir := stateDir()
	_ = os.MkdirAll(dir, 0700)
	_ = os.WriteFile(filepath.Join(dir, configCacheName), data, 0644)
	logger.Info("Config refreshed (version %d)", cfg.Version)
}

// -----------------------------------------------------------------
// helpers
// -----------------------------------------------------------------

type facts struct {
	OS          string
	OSVersion   string
	Hostname    string
	ConsoleUser string
}

func collectFacts() facts {
	h, _ := os.Hostname()
	f := facts{
		OS:       runtime.GOOS,
		Hostname: h,
	}
	if u := os.Getenv("USER"); u != "" {
		f.ConsoleUser = u
	} else if u := os.Getenv("USERNAME"); u != "" {
		f.ConsoleUser = u
	}
	return f
}

func backoff(attempt int) time.Duration {
	if attempt > 10 {
		attempt = 10
	}
	base := math.Pow(2, float64(attempt))
	jitter := rand.Float64() * base * 0.3
	d := time.Duration((base+jitter)*float64(time.Second))
	if d > 5*time.Minute {
		d = 5 * time.Minute
	}
	return d
}

func sleep(stop chan struct{}, d time.Duration) {
	select {
	case <-stop:
	case <-time.After(d):
	}
}

// fmt is used in ensureEnrolled above.
var _ = fmt.Sprintf

// -----------------------------------------------------------------
// main
// -----------------------------------------------------------------

func main() {
	cfg, err := config.Load()
	if err != nil {
		logger.Fatal("Config load: %v", err)
	}

	d := newDaemon(cfg)

	svcCfg := &service.Config{
		Name:        "MyPortalTrayService",
		DisplayName: "MyPortal Tray Service",
		Description: "Maintains the MyPortal helpdesk tray connection.",
	}

	svc, err := service.New(d, svcCfg)
	if err != nil {
		logger.Fatal("Service init: %v", err)
	}

	// When not running interactively, run as a proper service.
	if !service.Interactive() {
		if err := svc.Run(); err != nil {
			logger.Fatal("Service run: %v", err)
		}
		return
	}

	// Interactive / development mode.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	_ = d.Start(svc)
	<-stop
	_ = d.Stop(svc)
}
