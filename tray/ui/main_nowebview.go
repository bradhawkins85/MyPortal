//go:build nowebview

// main_nowebview.go provides the shared logic for the tray UI agent when
// built without the webview library (CGO=0, cross-compiled builds).
// On Windows a real system-tray icon is shown via getlantern/systray (pure Go).
// On other platforms the process stays alive headlessly and handles IPC only.
package main

import (
	"encoding/json"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/ipc"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

const configCacheName = "tray-config.json"

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

var (
	gConfig    *api.ConfigResponse
	gIPCConn   net.Conn
	gPortalURL string

	// onConfigChanged is set by the platform-specific runUI implementation to
	// rebuild the tray menu whenever a config_changed IPC message is received.
	onConfigChanged func(*api.ConfigResponse)
)

// refreshPortalURL re-reads the portal URL from the highest-precedence
// source available, in this order:
//  1. MYPORTAL_URL environment variable (always wins; never overwritten).
//  2. tray-state.json (written by the service after a successful enrolment).
//  3. HKLM\Software\MyPortal\Tray\PortalURL on Windows (written by the MSI
//     at install time).  This lets the tray fetch its icon from the server
//     on the very first launch, before the service has finished enrolling.
//
// Called both at startup and whenever a config_changed IPC message arrives.
func refreshPortalURL() {
	if envURL := os.Getenv("MYPORTAL_URL"); envURL != "" {
		gPortalURL = envURL
		logger.Debug("refreshPortalURL: using MYPORTAL_URL env (%s)", envURL)
		return
	}
	p := filepath.Join(stateDir(), "tray-state.json")
	data, err := os.ReadFile(p)
	if err == nil {
		var state struct {
			PortalURL string `json:"portal_url"`
		}
		if jsonErr := json.Unmarshal(data, &state); jsonErr != nil {
			logger.Warn("Failed to parse tray-state.json: %v", jsonErr)
		} else if state.PortalURL != "" {
			gPortalURL = state.PortalURL
			logger.Debug("refreshPortalURL: using portal_url from %s (%s)", p, state.PortalURL)
			return
		}
	} else {
		logger.Debug("refreshPortalURL: %s not readable yet (%v)", p, err)
	}
	if regURL := portalURLFromRegistry(); regURL != "" {
		gPortalURL = regURL
		logger.Debug("refreshPortalURL: using PortalURL from registry (%s)", regURL)
		return
	}
	logger.Debug("refreshPortalURL: no portal URL available from any source")
}

func main() {
	_ = logger.Init("ui")
	logger.Info("MyPortal Tray UI starting")

	refreshPortalURL()

	gConfig = loadCachedConfig()
	go connectIPC()

	// runUI is provided by the platform-specific file:
	//   tray_nowebview_windows.go  – shows a real systray icon on Windows
	//   tray_nowebview_other.go    – blocks headlessly on macOS/Linux
	runUI()
}

func defaultConfig() *api.ConfigResponse {
	return &api.ConfigResponse{
		Version: 0,
		Menu: []api.MenuNode{
			{Type: "label", Label: "MyPortal"},
			{Type: "separator"},
			{Type: "open_chat", Label: "Chat with helpdesk"},
		},
		ChatEnabled: true,
	}
}

func loadCachedConfig() *api.ConfigResponse {
	path := filepath.Join(stateDir(), configCacheName)
	data, err := os.ReadFile(path)
	if err != nil {
		logger.Debug("loadCachedConfig: %s not present yet (%v) — using built-in defaults", path, err)
		return defaultConfig()
	}
	var cfg api.ConfigResponse
	if err := json.Unmarshal(data, &cfg); err != nil {
		logger.Warn("loadCachedConfig: %s is corrupt (%v) — using built-in defaults", path, err)
		return defaultConfig()
	}
	logger.Debug("loadCachedConfig: loaded version=%d, menu_nodes=%d, chat_enabled=%t from %s",
		cfg.Version, len(cfg.Menu), cfg.ChatEnabled, path)
	return &cfg
}

func buildChatURL(roomID int) string {
	if gPortalURL == "" {
		return ""
	}
	if roomID > 0 {
		return gPortalURL + "/tray/chat?room=" + itoa(roomID)
	}
	return gPortalURL + "/chat"
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	buf := make([]byte, 0, 10)
	neg := n < 0
	if neg {
		n = -n
	}
	for n > 0 {
		buf = append([]byte{byte('0' + n%10)}, buf...)
		n /= 10
	}
	if neg {
		buf = append([]byte{'-'}, buf...)
	}
	return string(buf)
}

func connectIPC() {
	for {
		conn, err := ipc.Dial()
		if err != nil {
			logger.Debug("connectIPC: dial failed (%v) — retry in 5s", err)
			time.Sleep(5 * time.Second)
			continue
		}
		gIPCConn = conn
		logger.Info("IPC connected to service")
		handleIPCMessages(conn)
		gIPCConn = nil
		logger.Debug("connectIPC: connection closed — reconnecting in 3s")
		time.Sleep(3 * time.Second)
	}
}

func handleIPCMessages(conn net.Conn) {
	for {
		msg, err := ipc.ReadFrom(conn)
		if err != nil {
			logger.Debug("handleIPCMessages: read error (%v)", err)
			return
		}
		logger.Debug("handleIPCMessages: received type=%q", msg.Type)
		switch msg.Type {
		case "chat_open":
			var payload struct {
				RoomID int `json:"room_id"`
			}
			_ = json.Unmarshal(msg.Payload, &payload)
			openBrowser(buildChatURL(payload.RoomID))
		case "config_changed":
			refreshPortalURL()
			gConfig = loadCachedConfig()
			if onConfigChanged != nil {
				onConfigChanged(gConfig)
			}
		case "show_notification":
			var n struct {
				Title string `json:"title"`
				Body  string `json:"body"`
			}
			_ = json.Unmarshal(msg.Payload, &n)
			showOSNotification(n.Title, n.Body)
		}
	}
}
