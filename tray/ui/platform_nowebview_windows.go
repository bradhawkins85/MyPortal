//go:build nowebview && windows

// This file provides Windows-specific UI helper stubs for the nowebview
// (CGO=0) build.  cmd.exe and powershell.exe are started with the
// CREATE_NO_WINDOW flag so that no console window is ever visible to the user.
package main

import (
	"fmt"
	"os/exec"
	"syscall"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func openBrowser(url string) {
	cmd := exec.Command("cmd", "/c", "start", url)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
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
