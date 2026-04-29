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
)

func main() {
	_ = logger.Init("ui")

	gPortalURL = os.Getenv("MYPORTAL_URL")
	if p := filepath.Join(stateDir(), "tray-state.json"); gPortalURL == "" {
		if data, err := os.ReadFile(p); err == nil {
			var state struct {
				PortalURL string `json:"portal_url"`
			}
			_ = json.Unmarshal(data, &state)
			gPortalURL = state.PortalURL
		}
	}

	// Load cached config.
	gConfig = loadCachedConfig()

	// Connect to IPC server in background.
	go connectIPC()

	// systray.Run blocks until Quit is called.
	systray.Run(onTrayReady, onTrayExit)
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
	systray.SetTitle("MyPortal")
	systray.SetTooltip("MyPortal Helpdesk")
	// Use a simple built-in icon; real icon bytes loaded from branding URL in Phase 6.
	buildMenu(gConfig)
}

func onTrayExit() {
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
		addNode(node, cfg)
	}

	systray.AddSeparator()
	quitItem := systray.AddMenuItem("Quit", "Quit MyPortal Tray")
	go func() {
		<-quitItem.ClickedCh
		systray.Quit()
	}()
}

func addNode(node api.MenuNode, cfg *api.ConfigResponse) {
	switch node.Type {
	case "separator":
		systray.AddSeparator()

	case "label":
		item := systray.AddMenuItem(node.Label, "")
		item.Disable()

	case "link":
		item := systray.AddMenuItem(node.Label, node.URL)
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
		item := systray.AddMenuItem(label, "Open chat window")
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
		item := systray.AddMenuItem(label, "")
		go func(text string) {
			for range item.ClickedCh {
				showTextWindow("Information", text)
			}
		}(cfg.DisplayText)

	case "env_var":
		label := node.Label
		if label == "" {
			label = node.Name
		}
		item := systray.AddMenuItem(label, "Click to copy value")
		go func(varName string) {
			for range item.ClickedCh {
				val := os.Getenv(varName)
				if val == "" {
					val = "(not set)"
				}
				showTextWindow(varName, val)
			}
		}(node.Name)

	case "submenu":
		// systray doesn't natively support submenus in all implementations;
		// render children at the same level with an indented label for Phase 3.
		if node.Label != "" {
			item := systray.AddMenuItem("▸ "+node.Label, "")
			item.Disable()
		}
		for _, child := range node.Children {
			addNode(*child, cfg)
		}
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
			var payload struct {
				RoomID       int    `json:"room_id"`
				MatrixRoomID string `json:"matrix_room_id"`
				Subject      string `json:"subject"`
			}
			_ = json.Unmarshal(msg.Payload, &payload)
			chatURL := buildChatURL(payload.RoomID)
			openChatWindow(chatURL, gConfig)

		case "config_changed":
			// Re-read cached config. Menu rebuild on config_changed is deferred
			// until a systray library version that exposes ResetMenu is adopted.
			gConfig = loadCachedConfig()

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
