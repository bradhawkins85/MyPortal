//go:build nowebview

// This file provides stubs for UI helper functions when building without
// the webview/systray libraries (CGO=0 cross-compile mode).
package main

import (
	"fmt"
	"os/exec"
	"runtime"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func openBrowser(url string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("cmd", "/c", "start", url)
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
		chatURL = buildChatURL(0)
	}
	openBrowser(chatURL)
}

func showOSNotification(title, body string) {
	fmt.Printf("[notification] %s: %s\n", title, body)
}
