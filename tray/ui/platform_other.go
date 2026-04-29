//go:build !windows && !darwin && !nowebview

package main

import (
	"os/exec"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func openBrowser(url string) {
	_ = exec.Command("xdg-open", url).Start()
}

func showTextWindow(title, text string) {
	// Phase 3: simple fallback — log to stdout.
	_ = title
	_ = text
}

func openChatWindow(chatURL string, cfg *api.ConfigResponse) {
	if chatURL == "" {
		chatURL = buildChatURL(0)
	}
	openBrowser(chatURL)
}

func showOSNotification(title, body string) {
	_ = exec.Command("notify-send", title, body).Start()
}
