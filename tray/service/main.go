// myportal-tray-service is the privileged background daemon for the
// MyPortal Tray App.
//
// # Responsibilities
//
//   - Enrols the device with the MyPortal server using the install token
//     stored in the registry (Windows) or plist (macOS).
//   - Maintains a persistent WebSocket connection to /ws/tray/{device_uid}
//     with exponential back-off and jitter.
//   - Reports routine facts every 1–2 hours, independently of command delivery.
//   - Pulls /api/tray/config and caches it to disk; re-fetches on
//     config_changed WebSocket event.
//   - Dispatches server commands (chat_open, show_notification, config_changed)
//     to the UI agent over the local IPC socket.
//   - Installs the MyPortal RMM agent on an install_rmm_agent command.
//   - Runs the auto-update checker every 6 hours.
//
// # Platforms
//
//   - Windows: installed as a Windows Service running as LocalSystem.
//   - macOS:   installed as a LaunchDaemon running as root.
//   - Linux:   installed as a systemd unit running as root. On a desktop the
//     installer adds the tray UI, which XDG autostart opens in each user's
//     session. Servers (Proxmox VE and other headless Linux) get the service
//     only: chat and notifications are not shown, but check-ins, configuration,
//     commands, network scans, updates and the RMM agent install all work.
//
// Both use github.com/kardianos/service for the lifecycle abstraction.
package main

import (
	"context"
	cryptorand "crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/rand"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"sync"
	"syscall"
	"time"

	"github.com/gorilla/websocket"
	"github.com/kardianos/service"

	"github.com/bradhawkins85/myportal-tray/internal/agent"
	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/config"
	"github.com/bradhawkins85/myportal-tray/internal/defender"
	"github.com/bradhawkins85/myportal-tray/internal/healthcheck"
	"github.com/bradhawkins85/myportal-tray/internal/ipc"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"github.com/bradhawkins85/myportal-tray/internal/notify"
	"github.com/bradhawkins85/myportal-tray/internal/rmminstall"
	"github.com/bradhawkins85/myportal-tray/internal/scanner"
	"github.com/bradhawkins85/myportal-tray/internal/updater"
)

const (
	heartbeatInterval     = time.Hour
	configRefreshInterval = 6 * time.Hour
	configCacheName       = "tray-config.json"
	stateFileName         = "tray-state.json"
	pendingStateFileName  = "tray-enrolment-pending.json"
	// defenderPolicyStateFileName records the Defender exclusions and scan
	// schedule the agent applied, so it never removes settings it did not add.
	defenderPolicyStateFileName = "defender-policy.json"
)

var launchTrayUIForActiveUserFunc = launchTrayUIForActiveUser

// -----------------------------------------------------------------
// Persistent state (auth token between restarts)
// -----------------------------------------------------------------

type persistedState struct {
	DeviceUID string `json:"device_uid"`
	AuthToken string `json:"auth_token"`
	PortalURL string `json:"portal_url"`
}

var stateDir = func() string {
	switch runtime.GOOS {
	case "windows":
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "MyPortal", "tray")
	case "darwin":
		return "/Library/Application Support/MyPortal/Tray"
	default:
		return "/var/lib/myportal-tray"
	}
}

func loadState() *persistedState {
	return loadStateFile(stateFileName)
}

func loadStateFile(name string) *persistedState {
	path := filepath.Join(stateDir(), name)
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

func saveState(s persistedState) error {
	return saveStateFile(s, stateFileName)
}

func saveStateFile(s persistedState, name string) error {
	dir := stateDir()
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	if uiReadsState(runtime.GOOS) {
		_ = os.Chmod(dir, 0755)
	}
	data, _ := json.Marshal(s)
	path := filepath.Join(dir, name)
	mode := os.FileMode(0600)
	if uiReadsState(runtime.GOOS) {
		// The LaunchDaemon runs as root while the LaunchAgent runs as the
		// interactive user. The UI needs the device token to request the
		// short-lived chat and ticket-form URLs. macOS has no shared private
		// group for every possible console user, so make this device-scoped
		// credential readable (but never writable) by those local users.
		mode = 0644
	}
	if err := os.WriteFile(path, data, mode); err != nil {
		return err
	}
	// WriteFile preserves the permissions of an existing file. Apply the mode
	// explicitly so upgrades repair state files created by older releases.
	return os.Chmod(path, mode)
}

// repairPersistedState rewrites the enrolled state when it lacks the current
// portal URL or, on macOS, is not readable by the logged-in user. State is
// otherwise only written at enrolment, so devices enrolled by older releases
// kept a file the UI agent cannot get the portal URL from, and the UI could
// never download the tray icon.
func repairPersistedState(s *persistedState, portalURL string) {
	var mode os.FileMode
	if info, err := os.Stat(filepath.Join(stateDir(), stateFileName)); err == nil {
		mode = info.Mode().Perm()
	}
	if !persistedStateNeedsRewrite(s, portalURL, mode, runtime.GOOS) {
		return
	}
	if portalURL != "" {
		s.PortalURL = portalURL
	}
	saveState(*s)
	logger.Info("Updated persisted state for the UI agent (portal=%s)", s.PortalURL)
}

func persistedStateNeedsRewrite(s *persistedState, portalURL string, mode os.FileMode, goos string) bool {
	if portalURL != "" && s.PortalURL != portalURL {
		return true
	}
	return uiReadsState(goos) && mode != 0644
}

// -----------------------------------------------------------------
// Daemon
// -----------------------------------------------------------------

type daemon struct {
	cfg     *config.Config
	client  *api.Client
	updater *updater.Checker
	ipcSrv  *ipc.Server
	stopCh  chan struct{}

	pendingUIMu      sync.Mutex
	pendingUIMessage *ipc.Message

	networkScanMu sync.Mutex
	// rmmInstallMu serialises deployments and automatic RMM upgrades.
	rmmInstallMu sync.Mutex
	// defenderScanMu serialises Defender scans, which run in the background
	// because Start-MpScan blocks until a (possibly hours-long) scan finishes.
	defenderScanMu   sync.Mutex
	defenderStatusMu sync.Mutex
	manualCheckinMu  sync.Mutex
	// healthCheckMu lets one health check run at a time.
	healthCheckMu      sync.Mutex
	configMu           sync.RWMutex
	configRefreshMu    sync.Mutex
	cachedConfig       *api.ConfigResponse
	defenderCommands   chan struct{}
	defenderDetections chan struct{}
}

func newDaemon(cfg *config.Config) *daemon {
	// An upgrade by an older installer can leave the portal URL blank in the
	// install settings. The enrolled state still records it, so use that
	// rather than going offline until someone reinstalls.
	if cfg.PortalURL == "" {
		if s := loadState(); s != nil && s.PortalURL != "" {
			logger.Warn("Portal URL missing from install settings; using %s from the enrolled state", s.PortalURL)
			cfg.PortalURL = s.PortalURL
		}
	}
	return &daemon{
		cfg:                cfg,
		client:             api.New(cfg.PortalURL),
		stopCh:             make(chan struct{}),
		defenderCommands:   make(chan struct{}, 1),
		defenderDetections: make(chan struct{}, 1),
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
	logger.Info("MyPortal Tray Service starting (version %s)", updater.AgentVersion)

	// Start IPC server for the UI agent. A headless Linux install has no UI.
	var err error
	if headless() {
		logger.Info("Headless install: no tray UI, so chat and notifications are not shown on this device")
	} else if d.ipcSrv, err = ipc.NewServer(); err != nil {
		logger.Error("IPC server: %v — chat delivery disabled", err)
	} else {
		d.ipcSrv.On("refresh_config", func(msg ipc.Message) {
			logger.Info("refresh_config received from UI — re-fetching config")
			d.refreshConfigAndNotify()
		})
		d.ipcSrv.On("scan_network", func(msg ipc.Message) {
			logger.Info("Manual network scan request received from UI")
			go d.manualNetworkScan()
		})

		// Re-deliver the latest config_changed event to any UI agent
		// that connects after the initial broadcast already fired.
		// Without this, a UI agent that takes >5s to connect after the
		// service finishes enrolment would never know to re-read the
		// cached config and would keep showing the default menu /
		// default icon.
		d.ipcSrv.OnConnect(func(send func(ipc.Message)) {
			logger.Debug("ipc onConnect: replaying config_changed to new UI client")
			send(ipc.Message{Type: "config_changed"})
			if msg := d.consumePendingUIMessage(); msg != nil {
				logger.Info("ipc onConnect: replaying pending %s to newly launched UI client", msg.Type)
				send(*msg)
			}
		})
	}

	// Do not start authenticated workers until credentials have been received.
	if !retryEnrolment(d.stopCh, d.ensureEnrolled, backoff) {
		if d.ipcSrv != nil {
			d.ipcSrv.Close()
		}
		return
	}
	go d.configRefreshLoop()

	// Auto-update checker.
	updateCtx, cancelUpdate := context.WithCancel(context.Background())
	defer cancelUpdate()
	d.updater = updater.New(d.client, d.cfg.AutoUpdate)
	go d.updater.Run(updateCtx)
	go d.rmmVersionLoop(updateCtx)

	// Main WS + heartbeat loop.
	go cleanupHealthCheckPawnIO()
	go d.wsLoop()
	go d.heartbeatLoop()
	go d.defenderStatusLoop()
	go d.defenderCommandLoop()
	go d.defenderDetectionLoop()
	go d.networkScannerLoop()
	go d.trayUIWatchdogLoop()

	<-d.stopCh
	cancelUpdate()
	if d.ipcSrv != nil {
		d.ipcSrv.Close()
	}
	logger.Info("MyPortal Tray Service stopped")
}

func (d *daemon) defenderCommandLoop() {
	if runtime.GOOS != "windows" {
		return
	}
	for {
		d.processDefenderCommands()
		timer := time.NewTimer(routineInterval())
		select {
		case <-d.stopCh:
			timer.Stop()
			return
		case <-d.defenderCommands:
			timer.Stop()
		case <-timer.C:
		}
	}
}

func (d *daemon) processDefenderCommands() {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	policy, err := d.client.GetDefenderPolicy(ctx)
	cancel()
	if err != nil {
		// Fail closed: never run a privileged local command unless the portal
		// has just confirmed that this device is still managed.
		logger.Warn("Defender command policy: %v", err)
		return
	}
	if !policy.Enabled {
		return
	}

	ctx, cancel = context.WithTimeout(context.Background(), 15*time.Second)
	commands, err := d.client.GetDefenderCommands(ctx)
	cancel()
	if err != nil {
		logger.Warn("Defender command poll: %v", err)
		return
	}
	for _, command := range commands {
		// A device can be excluded after this poll claimed its commands. Check
		// again immediately before every execution so that an in-flight change
		// cannot cause a queued Defender PowerShell action to run later.
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		policy, err := d.client.GetDefenderPolicy(ctx)
		cancel()
		if err != nil {
			logger.Warn("Defender command %d policy check: %v", command.ID, err)
			return
		}
		if !policy.Enabled {
			logger.Info("Skipping Defender commands because this device is not managed")
			return
		}
		if defender.IsScan(command.CommandType) {
			go func(command api.DefenderCommand) {
				d.defenderScanMu.Lock()
				defer d.defenderScanMu.Unlock()
				// A queued scan may wait hours behind another one; confirm
				// the device is still managed before it starts.
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				policy, err := d.client.GetDefenderPolicy(ctx)
				cancel()
				if err != nil || !policy.Enabled {
					logger.Info("Skipping Defender command %d because management could not be confirmed", command.ID)
					return
				}
				d.executeDefenderCommand(command)
			}(command)
			continue
		}
		d.executeDefenderCommand(command)
	}
}

func (d *daemon) executeDefenderCommand(command api.DefenderCommand) {
	logger.Info("Executing Defender command %d (%s)", command.ID, command.CommandType)
	executeErr := defender.Execute(context.Background(), command.CommandType, command.DetectionUID)
	status := "completed"
	result := map[string]interface{}{"message": "Command completed successfully"}
	if executeErr != nil {
		status = "failed"
		result["message"] = executeErr.Error()
		logger.Warn("Defender command %d failed: %v", command.ID, executeErr)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	err := d.client.ReportDefenderCommandResult(ctx, command.ID, status, result)
	cancel()
	if err != nil {
		logger.Warn("Defender command %d result upload: %v", command.ID, err)
	}
}

func (d *daemon) defenderStatusLoop() {
	// Report immediately after service startup, then periodically. Previously
	// the service never called the Defender endpoints, leaving every enrolled
	// device permanently at "Awaiting report".
	for {
		d.reportDefenderStatus()
		timer := time.NewTimer(routineInterval())
		select {
		case <-d.stopCh:
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

func (d *daemon) reportDefenderStatus() {
	if runtime.GOOS != "windows" {
		return
	}
	if !d.defenderStatusMu.TryLock() {
		return
	}
	defer d.defenderStatusMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	policy, err := d.client.GetDefenderPolicy(ctx)
	cancel()
	if err != nil {
		logger.Warn("Defender policy: %v", err)
		return
	}
	if !policy.Enabled {
		return
	}
	// Reconcile the portal policy first so the status report carries the
	// outcome. The defender package skips any change that Tamper Protection
	// could block and reports it instead.
	ctx, cancel = context.WithTimeout(context.Background(), 10*time.Minute)
	policyResult, policyErr := defender.ApplyPolicy(ctx, *policy, filepath.Join(stateDir(), defenderPolicyStateFileName))
	cancel()
	if policyErr != nil {
		logger.Warn("Defender policy: %v", policyErr)
		policyResult = &api.DefenderPolicyResult{Status: "failed", EvaluatedAt: time.Now().UTC(), Items: []api.DefenderPolicyItem{{
			Setting: "policy", Action: "evaluate", Status: defender.StatusFailed, Message: policyErr.Error(),
		}}}
	}
	status, err := defender.Collect()
	if err != nil {
		logger.Warn("Defender status collection: %v", err)
		return
	}
	status.PolicyResult = policyResult
	ctx, cancel = context.WithTimeout(context.Background(), 15*time.Second)
	err = d.client.ReportDefenderStatus(ctx, status)
	cancel()
	if err != nil {
		logger.Warn("Defender status upload: %v", err)
		return
	}
	logger.Debug("Defender status reported (%s)", status.HealthStatus)
}

func (d *daemon) networkScannerLoop() {
	ticker := time.NewTicker(time.Minute)
	defer ticker.Stop()
	var lastScan time.Time
	for {
		cfg := d.currentConfig()
		if cfg != nil && cfg.NetworkScannerEnabled {
			interval := time.Duration(cfg.NetworkScanIntervalMinutes) * time.Minute
			if interval < 5*time.Minute {
				interval = 5 * time.Minute
			}
			if lastScan.IsZero() || time.Since(lastScan) >= interval {
				lastScan = time.Now()
				d.runNetworkScan("interval", cfg)
			}
		}

		select {
		case <-d.stopCh:
			return
		case <-ticker.C:
		}
	}
}

func (d *daemon) manualNetworkScan() {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	cfg, err := d.client.GetConfig(ctx)
	cancel()
	if err != nil {
		logger.Warn("Manual network scan config lookup: %v", err)
		return
	}
	if !cfg.NetworkScannerEnabled {
		logger.Warn("Manual network scan ignored: network scanning is not enabled for this device")
		return
	}
	d.runNetworkScan("manual", cfg)
}

func (d *daemon) runNetworkScan(source string, cfg *api.ConfigResponse) {
	if !d.networkScanMu.TryLock() {
		logger.Info("Network scan (%s) ignored: another scan is already running", source)
		return
	}
	defer d.networkScanMu.Unlock()

	started := time.Now()
	logger.Info("Network scan (%s) started", source)
	defer func() {
		logger.Info("Network scan (%s) stopped after %s", source, time.Since(started).Round(time.Millisecond))
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	wanIP, err := d.client.GetWANIP(ctx)
	cancel()
	if err != nil {
		logger.Warn("Network scan (%s) WAN IP lookup: %v", source, err)
		return
	}
	if !scanner.IPAllowed(wanIP, cfg.NetworkScanWANCIDRs) {
		logger.Warn("Network scan (%s) skipped: WAN IP %s is outside the configured ranges", source, wanIP)
		return
	}
	targets := scanner.AllowedTargets(cfg.NetworkScanLocalCIDRs)
	if len(targets) == 0 {
		logger.Warn("Network scan (%s) skipped: no configured local CIDR is connected", source)
		return
	}
	hosts, err := scanner.Scan(targets)
	if err != nil {
		logger.Warn("Network scan (%s): %v", source, err)
		return
	}
	ctx, cancel = context.WithTimeout(context.Background(), 30*time.Second)
	err = d.client.UploadNetworkScan(ctx, wanIP, targets, hosts)
	cancel()
	if err != nil {
		logger.Warn("Network scan (%s) upload: %v", source, err)
		return
	}
	logger.Info("Network scan (%s) uploaded (%d hosts)", source, len(hosts))
}

// retryEnrolment waits through temporary failures and stops with the service.
func retryEnrolment(stopCh chan struct{}, enrol func() error, delay func(int) time.Duration) bool {
	for attempt := 0; ; attempt++ {
		select {
		case <-stopCh:
			return false
		default:
		}
		if err := enrol(); err == nil {
			return true
		} else {
			wait := delay(attempt)
			logger.Error("Enrolment failed: %v — retry in %v", err, wait)
			sleep(stopCh, wait)
		}
	}
}

func (d *daemon) ensureEnrolled() error {
	s := loadState()
	if s != nil && s.AuthToken != "" {
		d.client.SetAuth(s.DeviceUID, s.AuthToken)
		logger.Info("Restored persisted auth (device_uid=%s)", s.DeviceUID)
		logger.Debug("ensureEnrolled: persisted state at %s, portal=%s", filepath.Join(stateDir(), stateFileName), s.PortalURL)
		repairPersistedState(s, d.cfg.PortalURL)
		// Ensure the device UID is present in HKLM for the UI agent and
		// any external tools that read HKLM\Software\MyPortal\Tray.
		saveDeviceUIDToRegistry(s.DeviceUID)
		return nil
	}

	if d.cfg.EnrolToken == "" {
		return fmt.Errorf("no enrol token configured")
	}

	// Keep pending identity separate from tray-state.json: installers and the
	// UI treat that file as proof of completed enrolment.
	if s == nil {
		s = loadStateFile(pendingStateFileName)
	}

	// Persist an identity before contacting the server. A timed-out request may
	// already have created a row; retries and service restarts must reuse it.
	if s == nil || s.DeviceUID == "" || (s.PortalURL != "" && s.PortalURL != d.cfg.PortalURL) {
		var identity [16]byte
		if _, err := cryptorand.Read(identity[:]); err != nil {
			return fmt.Errorf("generate device identity: %w", err)
		}
		s = &persistedState{DeviceUID: hex.EncodeToString(identity[:]), PortalURL: d.cfg.PortalURL}
		if err := saveStateFile(*s, pendingStateFileName); err != nil {
			return fmt.Errorf("persist pending enrolment identity: %w", err)
		}
	}

	facts := collectFacts()
	logger.Debug("ensureEnrolled: enrolling against %s as %s/%s", d.cfg.PortalURL, facts.OS, facts.Hostname)
	resp, err := d.client.Enrol(context.Background(), api.EnrolRequest{
		InstallToken: d.cfg.EnrolToken,
		DeviceUID:    s.DeviceUID,
		OS:           facts.OS,
		OSVersion:    facts.OSVersion,
		Hostname:     facts.Hostname,
		SerialNumber: facts.SerialNumber,
		AgentVersion: updater.AgentVersion,
	})
	if err != nil {
		return err
	}
	if err := saveState(persistedState{DeviceUID: resp.DeviceUID, AuthToken: resp.AuthToken, PortalURL: d.cfg.PortalURL}); err != nil {
		return fmt.Errorf("persist enrolment credentials: %w", err)
	}
	_ = os.Remove(filepath.Join(stateDir(), pendingStateFileName))
	saveDeviceUIDToRegistry(resp.DeviceUID)
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
	logger.Debug("WS message received: type=%q", msgType)
	switch msgType {
	case "ping":
		// nothing — pong is sent in the goroutine above
	case "config_changed":
		logger.Info("config_changed received — re-fetching config")
		go d.refreshConfigAndNotify()
	case "defender_commands":
		select {
		case d.defenderCommands <- struct{}{}:
		default:
		}
	case "checkin_now":
		go d.fullTrayCheckin()
	case "chat_open":
		logger.Info("chat_open received")
		rawPayload, _ := json.Marshal(msg)
		d.deliverUserSessionMessage(ipc.Message{
			Type:    "chat_open",
			Payload: json.RawMessage(rawPayload),
		})
	case "chat_message":
		logger.Info("chat_message received")
		rawPayload, _ := json.Marshal(msg)
		d.deliverUserSessionMessage(ipc.Message{
			Type:    "chat_message",
			Payload: json.RawMessage(rawPayload),
		})
	case "update":
		logger.Info("update command received — checking for tray update")
		if d.updater == nil {
			d.updater = updater.New(d.client, d.cfg.AutoUpdate)
		}
		go d.updater.CheckNow(context.Background(), true)
	case "show_notification":
		if d.ipcSrv != nil {
			payload := msg["payload"]
			var n notify.Notification
			if err := json.Unmarshal(payload, &n); err == nil {
				notify.Send(d.ipcSrv, n)
			}
		}
	case "troubleshoot":
		logger.Info("troubleshoot command received")
		go d.handleTroubleshoot(msg)
	case "health_check":
		logger.Info("health_check command received")
		go d.handleHealthCheck(msg)
	case "install_rmm_agent":
		logger.Info("install_rmm_agent command received")
		go d.handleInstallRMMAgent(msg)
	}
}

// rmmVersionLoop runs after every tray restart (including installer updates).
// Retry while offline or while release assets are still being published.
func (d *daemon) rmmVersionLoop(ctx context.Context) {
	if runtime.GOOS != "windows" && runtime.GOOS != "darwin" && runtime.GOOS != "linux" {
		return
	}
	ticker := time.NewTicker(2 * time.Hour)
	defer ticker.Stop()
	for {
		d.rmmInstallMu.Lock()
		checkCtx, cancel := context.WithTimeout(ctx, 15*time.Minute)
		changed, err := rmminstall.New(d.client).EnsureVersion(checkCtx, updater.AgentVersion)
		cancel()
		d.rmmInstallMu.Unlock()
		if err != nil && ctx.Err() == nil {
			logger.Warn("RMM version sync: %v; will retry", err)
		} else if changed {
			logger.Info("RMM agent updated to match tray version %s", updater.AgentVersion)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

// handleInstallRMMAgent installs the MyPortal RMM agent for an
// "install_rmm_agent" command and reports the outcome to the server.
func (d *daemon) handleInstallRMMAgent(msg map[string]json.RawMessage) {
	var commandID int
	if t, ok := msg["command_id"]; ok {
		_ = json.Unmarshal(t, &commandID)
	}
	if commandID <= 0 {
		logger.Warn("install_rmm_agent: ignoring command without a command id")
		return
	}
	d.rmmInstallMu.Lock()
	defer d.rmmInstallMu.Unlock()

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
	defer cancel()
	version, err := rmminstall.New(d.client).RepairVersion(ctx, updater.AgentVersion)
	message := fmt.Sprintf("Installed and verified the MyPortal RMM agent %s.", version)
	if err != nil {
		logger.Error("install_rmm_agent: %v", err)
		message = err.Error()
	} else {
		logger.Info("install_rmm_agent: installed version %s", version)
	}
	reportCtx, reportCancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer reportCancel()
	if postErr := d.client.PostRMMAgentInstallResult(reportCtx, commandID, err == nil, message); postErr != nil {
		logger.Warn("install_rmm_agent: reporting the result failed: %v", postErr)
	}
}

// handleTroubleshoot runs the AI troubleshooting agent for a "troubleshoot"
// WebSocket command and reports the result back to the server. It runs in a
// goroutine (see the dispatch switch) because collecting up to 24 h of logs
// plus a local LLM call can take several minutes and must never block the
// WebSocket read loop.
func (d *daemon) handleTroubleshoot(msg map[string]json.RawMessage) {
	var req agent.Request
	if t, ok := msg["ticket_id"]; ok {
		_ = json.Unmarshal(t, &req.TicketID)
	}
	if t, ok := msg["command_id"]; ok {
		_ = json.Unmarshal(t, &req.CommandID)
	}
	if t, ok := msg["prompt"]; ok {
		_ = json.Unmarshal(t, &req.Prompt)
	}
	var llm agent.LLMConfig
	for _, field := range []struct {
		key string
		dst *string
	}{
		{"llm_base_url", &llm.BaseURL},
		{"llm_model", &llm.Model},
		{"llm_api_key", &llm.APIKey},
	} {
		if t, ok := msg[field.key]; ok {
			_ = json.Unmarshal(t, field.dst)
		}
	}
	req.LLM = llm
	if t, ok := msg["mode"]; ok {
		_ = json.Unmarshal(t, &req.Mode)
	}
	if t, ok := msg["log_requests"]; ok {
		_ = json.Unmarshal(t, &req.LogRequests)
	}
	if t, ok := msg["extra_log_folders"]; ok {
		_ = json.Unmarshal(t, &req.ExtraLogFolders)
	}
	if t, ok := msg["extra_event_logs"]; ok {
		_ = json.Unmarshal(t, &req.ExtraEventLogs)
	}

	logger.Info("troubleshoot: running agent (ticket %d, command %d, model %q)",
		req.TicketID, req.CommandID, llm.Model)

	if req.TicketID <= 0 || req.CommandID <= 0 {
		logger.Warn("troubleshoot: ignoring command with missing ticket/command id (ticket %d, command %d)",
			req.TicketID, req.CommandID)
		return
	}

	ag := agent.NewAgent()
	endpoint := ""
	if h, err := os.Hostname(); err == nil {
		endpoint = h
	}
	report := func(stage, message string) {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if err := d.client.PostTroubleshootStatus(ctx, req.TicketID, req.CommandID, stage, message, endpoint); err != nil {
			logger.Warn("troubleshoot: status %q report failed: %v", stage, err)
		}
	}
	ag.OnProgress = report

	if req.Mode == agent.ModeCollectLogs {
		report("received", fmt.Sprintf("Troubleshooter agent %s started collecting the requested logs.", updater.AgentVersion))
	} else {
		report("received", fmt.Sprintf("Troubleshooter agent %s started (model %q).", updater.AgentVersion, llm.Model))
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()

	result, runErr := ag.Run(ctx, req)
	errMsg := ""
	if runErr != nil {
		// The agent still returns whatever it managed to produce; log the
		// partial failure and report below so the ticket gets the degraded
		// result (guidance without bundle, or bundle without guidance) along
		// with the reason.
		logger.Warn("troubleshoot: agent partial failure: %v", runErr)
		errMsg = runErr.Error()
	}

	var postErr error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Duration(attempt*5) * time.Second)
		}
		postCtx, postCancel := context.WithTimeout(context.Background(), 60*time.Second)
		postErr = d.client.PostTroubleshootComplete(postCtx, req.TicketID, req.CommandID,
			result.Guidance, result.Endpoint, errMsg, result.LogBundle)
		postCancel()
		if postErr == nil {
			break
		}
		logger.Warn("troubleshoot: report back failed (attempt %d): %v", attempt+1, postErr)
	}
	if postErr != nil {
		report("failed", fmt.Sprintf("The agent finished but could not upload its result: %v", postErr))
		return
	}
	logger.Info("troubleshoot: reported ticket %d (guidance=%d bytes, bundle=%d bytes, truncated=%t)",
		req.TicketID, len(result.Guidance), len(result.LogBundle), result.Truncated)
}

// handleHealthCheck runs one phase of an AI health check for a
// "health_check" WebSocket command and uploads the result. Only one health
// check runs at a time; it may briefly install PawnIO (see package
// healthcheck), so a panic is recovered and reported rather than taking the
// service down.
func (d *daemon) handleHealthCheck(msg map[string]json.RawMessage) {
	var req healthcheck.Request
	for _, field := range []struct {
		key string
		dst any
	}{
		{"command_id", &req.CommandID},
		{"health_check_id", &req.HealthCheckID},
		{"phase", &req.Phase},
		{"log_requests", &req.LogRequests},
		{"extra_log_folders", &req.ExtraLogFolders},
		{"extra_event_logs", &req.ExtraEventLogs},
		{"allow_pawnio", &req.AllowPawnIO},
		{"event_days", &req.EventDays},
	} {
		if t, ok := msg[field.key]; ok {
			_ = json.Unmarshal(t, field.dst)
		}
	}
	if req.CommandID <= 0 || req.HealthCheckID <= 0 {
		logger.Warn("health_check: ignoring command with missing ids (check %d, command %d)", req.HealthCheckID, req.CommandID)
		return
	}
	report := func(stage, message string) {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if err := d.client.PostHealthCheckStatus(ctx, req.HealthCheckID, req.CommandID, stage, message); err != nil {
			logger.Warn("health_check: status %q report failed: %v", stage, err)
		}
	}
	if !d.healthCheckMu.TryLock() {
		report(healthcheck.StageFailed, "Another health check is already running on this device.")
		return
	}
	defer d.healthCheckMu.Unlock()
	defer func() {
		if r := recover(); r != nil {
			logger.Error("health_check: panic: %v", r)
			report(healthcheck.StageFailed, fmt.Sprintf("The health check stopped unexpectedly: %v", r))
		}
	}()

	logger.Info("health_check: running %s phase (check %d, command %d, PawnIO allowed %t)",
		req.Phase, req.HealthCheckID, req.CommandID, req.AllowPawnIO)
	report(healthcheck.StageReceived, fmt.Sprintf("Health check agent %s started the %s phase.", updater.AgentVersion, req.Phase))

	runner := healthcheck.NewRunner(stateDir(), updater.AgentVersion)
	runner.OnProgress = report
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Minute)
	result, runErr := runner.Run(ctx, req)
	cancel()
	errMsg := ""
	if runErr != nil {
		logger.Warn("health_check: partial failure: %v", runErr)
		errMsg = runErr.Error()
		if r := []rune(errMsg); len(r) > 4000 {
			errMsg = string(r[:4000])
		}
	}
	if len(result.Snapshot) == 0 && len(result.LogBundle) == 0 {
		if errMsg == "" {
			errMsg = "The health check produced nothing to upload."
		}
		report(healthcheck.StageFailed, errMsg)
		return
	}
	report(healthcheck.StageUploading, fmt.Sprintf("Uploading %d bytes of health metrics and %d bytes of logs.", len(result.Snapshot), len(result.LogBundle)))
	var postErr error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Duration(attempt*5) * time.Second)
		}
		postCtx, postCancel := context.WithTimeout(context.Background(), 2*time.Minute)
		postErr = d.client.PostHealthCheckResult(postCtx, req.HealthCheckID, req.CommandID, req.Phase,
			result.Snapshot, result.LogBundle, errMsg)
		postCancel()
		if postErr == nil {
			break
		}
		logger.Warn("health_check: upload failed (attempt %d): %v", attempt+1, postErr)
	}
	if postErr != nil {
		report(healthcheck.StageFailed, fmt.Sprintf("The health check finished but could not upload its result: %v", postErr))
		return
	}
	logger.Info("health_check: uploaded check %d (%s phase, snapshot=%d bytes, logs=%d bytes)",
		req.HealthCheckID, req.Phase, len(result.Snapshot), len(result.LogBundle))
}

// cleanupHealthCheckPawnIO removes PawnIO if a health check installed it and
// the service stopped before removing it.
func cleanupHealthCheckPawnIO() {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	found, err := healthcheck.CleanupPawnIO(ctx, stateDir())
	switch {
	case err != nil:
		logger.Warn("health_check: removing PawnIO left by an interrupted health check failed: %v", err)
	case found:
		logger.Info("health_check: removed PawnIO left by an interrupted health check")
	}
}

func (d *daemon) deliverUserSessionMessage(msg ipc.Message) {
	if headless() {
		logger.Info("%s received, but this headless install has no tray UI to show it", msg.Type)
		return
	}
	delivered := 0
	if d.ipcSrv != nil {
		delivered = d.ipcSrv.Broadcast(msg)
	}
	if delivered > 0 {
		return
	}

	d.pendingUIMu.Lock()
	pending := msg
	d.pendingUIMessage = &pending
	d.pendingUIMu.Unlock()

	logger.Warn("%s received but no UI agent is connected; attempting to launch per-user tray UI", msg.Type)
	if err := launchTrayUIForActiveUserFunc(); err != nil {
		logger.Warn("launchTrayUIForActiveUser failed: %v", err)
	}
}

func (d *daemon) consumePendingUIMessage() *ipc.Message {
	d.pendingUIMu.Lock()
	defer d.pendingUIMu.Unlock()
	if d.pendingUIMessage == nil {
		return nil
	}
	msg := *d.pendingUIMessage
	d.pendingUIMessage = nil
	return &msg
}

func (d *daemon) heartbeatLoop() {
	for {
		facts := collectFacts()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		if err := d.client.Heartbeat(ctx, api.HeartbeatRequest{
			ConsoleUser: facts.ConsoleUser, AgentVersion: updater.AgentVersion,
		}); err != nil {
			logger.Warn("Heartbeat: %v", err)
		}
		cancel()
		timer := time.NewTimer(routineInterval())
		select {
		case <-d.stopCh:
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

// Spread routine reports across the fleet without delaying urgent channels.
func routineInterval() time.Duration {
	return heartbeatInterval + time.Duration(rand.Int63n(int64(time.Hour)))
}

func (d *daemon) configRefreshLoop() {
	for {
		wait := configRefreshInterval + time.Duration(rand.Int63n(int64(30*time.Minute)))
		if !d.refreshConfigAndNotify() {
			wait = time.Minute + time.Duration(rand.Int63n(int64(4*time.Minute)))
		}
		timer := time.NewTimer(wait)
		select {
		case <-d.stopCh:
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}

func (d *daemon) currentConfig() *api.ConfigResponse {
	d.configMu.RLock()
	defer d.configMu.RUnlock()
	return d.cachedConfig
}

func (d *daemon) refreshConfigAndNotify() bool {
	if !d.refreshConfig() {
		return false
	}
	if d.ipcSrv != nil {
		d.ipcSrv.Broadcast(ipc.Message{Type: "config_changed"})
	}
	return true
}

func (d *daemon) refreshConfig() bool {
	d.configRefreshMu.Lock()
	defer d.configRefreshMu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	logger.Debug("refreshConfig: GET %s/api/tray/config", d.cfg.PortalURL)
	cfg, err := d.client.GetConfig(ctx)
	if err != nil {
		logger.Warn("GetConfig: %v", err)
		return false
	}
	d.configMu.Lock()
	d.cachedConfig = cfg
	d.configMu.Unlock()
	// Persist config to disk for the UI agent to read.
	data, _ := json.Marshal(cfg)
	dir := stateDir()
	_ = os.MkdirAll(dir, 0700)
	path := filepath.Join(dir, configCacheName)
	_ = os.WriteFile(path, data, 0644)
	logger.Info("Config refreshed (version %d)", cfg.Version)
	logger.Debug("refreshConfig: wrote %d bytes to %s, menu_nodes=%d, chat_enabled=%t, branding_icon_url=%q",
		len(data), path, len(cfg.Menu), cfg.ChatEnabled, cfg.BrandingIconURL)
	return true
}

// -----------------------------------------------------------------
// helpers
// -----------------------------------------------------------------

type facts struct {
	OS           string
	OSVersion    string
	Hostname     string
	SerialNumber string
	ConsoleUser  string
}

func collectFacts() facts {
	h, _ := os.Hostname()
	f := facts{
		OS:       runtime.GOOS,
		Hostname: h,
	}
	f.ConsoleUser = consoleUserName()
	platformFacts(&f)
	return f
}

func backoff(attempt int) time.Duration {
	if attempt > 10 {
		attempt = 10
	}
	base := math.Pow(2, float64(attempt))
	jitter := rand.Float64() * base * 0.3
	d := time.Duration((base + jitter) * float64(time.Second))
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
	if err := logger.Init("service"); err != nil {
		logger.Error("logger init: %v", err)
	}

	// Handle install/uninstall subcommands before loading config so
	// that a re-install after a failed prior install can still
	// clean up (or skip cleaning up) without requiring the
	// registry/env config that the daemon needs.
	if len(os.Args) > 1 {
		cmd := os.Args[1]
		if cmd == "install" || cmd == "uninstall" {
			handleServiceCommand(cmd)
			return
		}
	}

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
		// Also repairs services installed by the MSI or older versions.
		if err := configureServiceResilience(svcCfg.Name); err != nil {
			logger.Warn("Configure service recovery: %v", err)
		}
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

// handleServiceCommand runs the "install" or "uninstall" subcommand
// using the OS service manager. For "uninstall" it first checks
// whether the service is registered; a missing service (e.g. after a
// previous install that failed before the service was created) is
// treated as a successful no-op rather than an error.
func handleServiceCommand(cmd string) {
	svcCfg := &service.Config{
		Name:        "MyPortalTrayService",
		DisplayName: "MyPortal Tray Service",
		Description: "Maintains the MyPortal helpdesk tray connection.",
	}
	// The Start/Stop callbacks are never invoked during install or
	// uninstall, so a no-op implementation is safe.
	svc, err := service.New(&noopLifecycle{}, svcCfg)
	if err != nil {
		logger.Fatal("Service init: %v", err)
	}

	if cmd == "uninstall" {
		_, statusErr := svc.Status()
		if statusErr != nil && errors.Is(statusErr, service.ErrNotInstalled) {
			logger.Info("Service is not installed — nothing to remove.")
			return
		}
		// Stop the service if it is running. The call may fail if
		// the service is already stopped; that is not an error for
		// uninstall purposes.
		if stopErr := svc.Stop(); stopErr != nil {
			logger.Warn("Stop before uninstall: %v", stopErr)
		}
	}

	if err := service.Control(svc, cmd); err != nil {
		logger.Error("%s failed: %v", cmd, err)
		os.Exit(1)
	}
	if cmd == "install" {
		if err := configureServiceResilience(svcCfg.Name); err != nil {
			logger.Error("Configure service recovery: %v", err)
			os.Exit(1)
		}
	} else if err := removeSafeBootRegistration(svcCfg.Name); err != nil {
		logger.Warn("Remove Safe Mode registration: %v", err)
	}
	logger.Info("%s succeeded", cmd)
}

// noopLifecycle satisfies service.Interface without starting or
// stopping any daemon work. It is used for install/uninstall
// commands that only need the service-manager handle.
type noopLifecycle struct{}

func (noopLifecycle) Start(service.Service) error { return nil }
func (noopLifecycle) Stop(service.Service) error  { return nil }
