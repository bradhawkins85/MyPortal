//go:build nowebview

// main_nowebview.go provides a minimal tray UI agent that runs without
// the systray or webview libraries (CGO=0, cross-compiled builds).
// The agent still connects to the IPC socket and opens chat/browser URLs
// via the OS default handler.
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
)

func main() {
	_ = logger.Init("ui")
	logger.Info("MyPortal Tray UI (headless/nowebview mode) starting")

	gPortalURL = os.Getenv("MYPORTAL_URL")
	gConfig = loadCachedConfig()
	go connectIPC()

	// Block forever — the service will send an OS signal to terminate.
	select {}
}

func loadCachedConfig() *api.ConfigResponse {
	path := filepath.Join(stateDir(), configCacheName)
	data, err := os.ReadFile(path)
	if err != nil {
		return &api.ConfigResponse{ChatEnabled: true}
	}
	var cfg api.ConfigResponse
	if err := json.Unmarshal(data, &cfg); err != nil {
		return &api.ConfigResponse{ChatEnabled: true}
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
