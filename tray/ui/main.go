//go:build !nowebview

// myportal-tray-ui is the per-session tray UI agent for the MyPortal
// Tray App.  It is started once per interactive console session by the
// privileged tray service.
//
// Responsibilities:
//   - Read the cached config from disk (written by the service).
//   - Render the system tray icon and menu from payload_json.
//   - Connect to the local IPC socket and react to commands from the
//     service (chat_open, show_notification, config_changed).
//   - Open a webview window for the MyPortal /tray/chat page when a
//     chat is opened.
package main

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"sync/atomic"
	"time"

	"github.com/getlantern/systray"

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
	gDeviceUID string
	gAuthToken string

	// trayReady is set once systray has created its hidden window and notify
	// icon. The service can replay config_changed immediately after IPC connect,
	// which may happen before systray.Run finishes native initialization.
	trayReady atomic.Bool
)

func main() {
	_ = logger.Init("ui")
	logger.Info("MyPortal Tray UI (webview) starting")

	refreshPortalURL()
	refreshDeviceUID()
	refreshAuthToken()

	// Load cached config.
	gConfig = loadCachedConfig()

	// Connect to IPC server in background.
	go connectIPC()

	// systray.Run blocks until Quit is called.
	systray.Run(onTrayReady, onTrayExit)
}

// requestChatTokenForRoom calls POST /api/tray/chat-token to obtain a
// short-lived one-time URL token for the popup chat window.
// Returns the full chat URL on success, or "" on failure.
func requestChatTokenForRoom(roomID int) string {
	if gPortalURL == "" || gAuthToken == "" {
		return ""
	}
	var bodyBytes []byte
	if roomID > 0 {
		bodyBytes = []byte(`{"room_id":` + itoa(roomID) + `}`)
	} else {
		bodyBytes = []byte(`{}`)
	}
	req, err := newHTTPRequest("POST", gPortalURL+"/api/tray/chat-token", bodyBytes)
	if err != nil {
		return ""
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return ""
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return ""
	}
	var result struct {
		ChatURL string `json:"chat_url"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return ""
	}
	return result.ChatURL
}

func loadCachedConfig() *api.ConfigResponse {
	path := filepath.Join(stateDir(), configCacheName)
	data, err := os.ReadFile(path)
	if err != nil {
		return defaultConfig()
	}
	var cfg api.ConfigResponse
	if err := json.Unmarshal(data, &cfg); err != nil {
		return defaultConfig()
	}
	return &cfg
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

func onTrayReady() {
	trayReady.Store(true)
	systray.SetTitle("MyPortal")
	systray.SetTooltip(trayTooltip(gConfig))
	// Use a simple built-in icon; real icon bytes loaded from branding URL in Phase 6.
	buildMenu(gConfig)
}

func onTrayExit() {
	trayReady.Store(false)
	if gIPCConn != nil {
		gIPCConn.Close()
	}
}

// buildMenu constructs the systray menu from a ConfigResponse.
func buildMenu(cfg *api.ConfigResponse) {
	if cfg == nil {
		cfg = defaultConfig()
	}

	for _, node := range cfg.Menu {
		addNode(node, cfg, nil)
	}
}

func requestConfigRefresh() {
	if gIPCConn == nil {
		logger.Warn("Refresh requested but IPC is not connected")
		return
	}
	if err := ipc.SendTo(gIPCConn, ipc.Message{Type: "refresh_config"}); err != nil {
		logger.Warn("Refresh request failed: %v", err)
	}
}

func addTrayMenuItem(parent *systray.MenuItem, label string, tooltip string) *systray.MenuItem {
	if parent != nil {
		return parent.AddSubMenuItem(label, tooltip)
	}
	return systray.AddMenuItem(label, tooltip)
}

func addTraySeparator(parent *systray.MenuItem) {
	if parent == nil {
		systray.AddSeparator()
		return
	}
	item := parent.AddSubMenuItem("────────", "")
	item.Disable()
}

func addNode(node api.MenuNode, cfg *api.ConfigResponse, parent *systray.MenuItem) {
	switch node.Type {
	case "separator":
		addTraySeparator(parent)

	case "label":
		item := addTrayMenuItem(parent, node.Label, "")
		if node.Color == "" {
			item.Disable()
		}

	case "app_version":
		item := addTrayMenuItem(parent, versionMenuLabel(node), "")
		item.Disable()

	case "link":
		item := addTrayMenuItem(parent, node.Label, node.URL)
		go func(url string) {
			for range item.ClickedCh {
				openBrowser(url)
			}
		}(node.URL)

	case "open_chat":
		if !cfg.ChatEnabled {
			return
		}
		label := node.Label
		if label == "" {
			label = "Chat with helpdesk"
		}
		item := addTrayMenuItem(parent, label, "Open chat window")
		go func() {
			for range item.ClickedCh {
				openChatWindow("", cfg)
			}
		}()

	case "display_text":
		label := node.Label
		if label == "" {
			label = "Info"
		}
		item := addTrayMenuItem(parent, label, "")
		go func(text string) {
			for range item.ClickedCh {
				showTextWindow("Information", text)
			}
		}(cfg.DisplayText)

	case "env_var":
		label := resolveEnvVarMenuLabel(node)
		item := addTrayMenuItem(parent, label, "Click to copy value")
		go func(varName string) {
			for range item.ClickedCh {
				val := os.Getenv(varName)
				if val == "" {
					val = "(not set)"
				}
				showTextWindow(varName, val)
			}
		}(normalizeEnvVarName(node.Name))

	case "submenu":
		label := node.Label
		if label == "" {
			label = "Menu"
		}
		item := addTrayMenuItem(parent, label, "")
		for _, child := range node.Children {
			if child == nil {
				continue
			}
			addNode(*child, cfg, item)
		}

	case "submit_ticket":
		label := node.Label
		if label == "" {
			label = "Submit Ticket"
		}
		item := addTrayMenuItem(parent, label, "Submit a support ticket")
		go func() {
			for range item.ClickedCh {
				go openNewTicketWindow(cfg)
			}
		}()

	case "TRMM_Script", "trmm_script":
		label := node.Label
		if label == "" {
			label = node.ScriptName
		}
		if label == "" {
			label = "Run Tactical RMM Script"
		}
		item := addTrayMenuItem(parent, label, "Run a Tactical RMM script on this computer")
		go func(menuNode api.MenuNode) {
			for range item.ClickedCh {
				go runTRMMScriptFromMenu(menuNode)
			}
		}(node)

	case "refresh_config":
		label := node.Label
		if label == "" {
			label = "Refresh"
		}
		item := addTrayMenuItem(parent, label, "Refresh tray menu from server")
		go func() {
			for range item.ClickedCh {
				requestConfigRefresh()
			}
		}()

	case "quit":
		label := node.Label
		if label == "" {
			label = "Quit"
		}
		item := addTrayMenuItem(parent, label, "Exit MyPortal tray")
		go func() {
			for range item.ClickedCh {
				systray.Quit()
			}
		}()
	}
}

// connectIPC dials the service IPC socket and processes incoming messages.
func connectIPC() {
	for {
		conn, err := ipc.Dial()
		if err != nil {
			time.Sleep(5 * time.Second)
			continue
		}
		gIPCConn = conn
		logger.Info("IPC connected to service")
		handleIPCMessages(conn)
		gIPCConn = nil
		time.Sleep(3 * time.Second)
	}
}

func handleIPCMessages(conn net.Conn) {
	for {
		msg, err := ipc.ReadFrom(conn)
		if err != nil {
			return
		}
		switch msg.Type {
		case "chat_open":
			var payload chatOpenPayload
			_ = json.Unmarshal(msg.Payload, &payload)
			handleChatOpen(payload)

		case "chat_message":
			var payload chatMessagePayload
			_ = json.Unmarshal(msg.Payload, &payload)
			handleChatMessage(payload)

		case "config_changed":
			refreshPortalURL()
			refreshDeviceUID()
			refreshAuthToken()
			// Re-read cached config. Menu rebuild on config_changed is deferred
			// until a systray library version that exposes ResetMenu is adopted.
			gConfig = loadCachedConfig()
			if trayReady.Load() {
				systray.SetTooltip(trayTooltip(gConfig))
			} else {
				logger.Debug("config_changed: received before tray ready; deferring tooltip update")
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
	return fmt.Sprintf("%d", n)
}
