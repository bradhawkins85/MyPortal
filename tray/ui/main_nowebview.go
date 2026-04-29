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

func main() {
	_ = logger.Init("ui")
	logger.Info("MyPortal Tray UI starting")

	// Prefer the environment variable, then fall back to the state file written
	// by the service.
	gPortalURL = os.Getenv("MYPORTAL_URL")
	if gPortalURL == "" {
		p := filepath.Join(stateDir(), "tray-state.json")
		if data, err := os.ReadFile(p); err == nil {
			var state struct {
				PortalURL string `json:"portal_url"`
			}
			if err := json.Unmarshal(data, &state); err != nil {
				logger.Warn("Failed to parse tray-state.json: %v", err)
			} else {
				gPortalURL = state.PortalURL
			}
		}
	}

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
		return defaultConfig()
	}
	var cfg api.ConfigResponse
	if err := json.Unmarshal(data, &cfg); err != nil {
		return defaultConfig()
	}
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
			time.Sleep(5 * time.Second)
			continue
		}
		gIPCConn = conn
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
				RoomID int `json:"room_id"`
			}
			_ = json.Unmarshal(msg.Payload, &payload)
			openBrowser(buildChatURL(payload.RoomID))
		case "config_changed":
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
