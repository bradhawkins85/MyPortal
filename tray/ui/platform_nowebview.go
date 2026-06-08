//go:build nowebview && !windows && !darwin

// This file provides stubs for UI helper functions when building without
// the webview/systray libraries (CGO=0 cross-compile mode) on non-Windows,
// non-macOS platforms (Linux / other).
package main

import (
	"fmt"
	"os/exec"
	"runtime"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

func openBrowser(url string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		cmd = exec.Command("open", url)
	default:
		cmd = exec.Command("xdg-open", url)
	}
	_ = cmd.Start()
}

func showTextWindow(title, text string) {
	fmt.Printf("[%s] %s\n", title, text)
}

func openChatWindow(chatURL string, _ *api.ConfigResponse) {
	if chatURL == "" {
		// Request a short-lived one-time auth token from the portal.  If the
		// request fails (portal unreachable, device not enrolled, etc.) we do
		// not fall back to an unauthenticated URL: the /chat staff endpoint
		// requires a portal login and is not useful for tray end-users.
		authedURL := requestChatTokenForRoom(0)
		if authedURL == "" {
			logger.Warn("openChatWindow: could not obtain chat token — portal may be unreachable or device not enrolled")
			return
		}
		chatURL = authedURL
	}
	openBrowser(chatURL)
}

func showOSNotification(title, body string) {
	fmt.Printf("[notification] %s: %s\n", title, body)
}
