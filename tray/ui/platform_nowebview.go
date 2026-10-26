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

// showTextWindow shows text in a zenity or kdialog window, falling back to a
// desktop notification.
func showTextWindow(title, text string) {
	for _, args := range [][]string{
		{"zenity", "--info", "--no-markup", "--title", title, "--text", text},
		{"kdialog", "--title", title, "--msgbox", text},
	} {
		if _, err := exec.LookPath(args[0]); err == nil {
			cmd := exec.Command(args[0], args[1:]...)
			if err := cmd.Start(); err == nil {
				go cmd.Wait()
				return
			}
		}
	}
	showOSNotification(title, text)
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

// showOSNotification uses notify-send (libnotify), which every common Linux
// desktop provides.
func showOSNotification(title, body string) {
	if _, err := exec.LookPath("notify-send"); err == nil {
		if err := exec.Command("notify-send", "--app-name=MyPortal", "--", title, body).Run(); err == nil {
			return
		}
	}
	fmt.Printf("[notification] %s: %s\n", title, body)
}
