//go:build nowebview && windows

// This file provides Windows-specific UI helper stubs for the nowebview
// (CGO=0) build.  cmd.exe and powershell.exe are started with the
// CREATE_NO_WINDOW flag so that no console window is ever visible to the user.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

func openBrowser(url string) {
	cmd := exec.Command("cmd", "/c", "start", url)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
	_ = cmd.Start()
}

func showTextWindow(title, text string) {
	fmt.Printf("[%s] %s\n", title, text)
}

func openChatWindow(chatURL string, _ *api.ConfigResponse) {
	if chatURL == "" {
		// Try to get an authenticated popup URL; fall back to the plain chat URL.
		authedURL := requestChatTokenForRoom(0)
		if authedURL != "" {
			chatURL = authedURL
		} else {
			chatURL = buildChatURL(0)
		}
	}
	if chatURL == "" {
		return
	}
	openChatAppWindow(chatURL)
}

func openChatAppWindow(chatURL string) {
	edgeArgs := []string{
		"--app=" + chatURL,
		"--window-size=920,680",
		"--disable-dev-tools",
		"--disable-extensions",
		"--no-first-run",
		"--no-default-browser-check",
		"--user-data-dir=" + filepath.Join(os.TempDir(), "MyPortal", "tray-chat-profile"),
	}

	if gPortalURL != "" {
		iconURL := strings.TrimRight(gPortalURL, "/") + "/tray/icon.ico"
		edgeArgs = append(edgeArgs,
			"--app-name=MyPortal Chat",
			"--app-icon-url="+iconURL,
		)
	}

	cmd := exec.Command("msedge", edgeArgs...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
	if err := cmd.Start(); err == nil {
		return
	} else {
		logger.Warn("openChatAppWindow: msedge launch failed (%v), falling back to browser", err)
	}
	openBrowser(chatURL)
}

func showOSNotification(title, body string) {
	fmt.Printf("[notification] %s: %s\n", title, body)
}
