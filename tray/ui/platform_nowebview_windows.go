//go:build nowebview && windows

// This file provides Windows-specific UI helper stubs for the nowebview
// (CGO=0) build.  Chat is opened in a dedicated app window rather than the
// default browser using a three-tier strategy:
//
//  1. Dedicated chat shell (myportal-tray-chat.exe) — best isolation.
//  2. Edge or Chrome in --app= mode with an isolated --user-data-dir.
//  3. Default browser — emergency fallback only.
//
// cmd.exe and powershell.exe are started with the CREATE_NO_WINDOW flag so
// that no console window is ever visible to the user.
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

	// "browser" mode: skip the dedicated client and open directly.
	if chatClientMode() == "browser" {
		logger.Debug("openChatWindow: browser mode — opening in default browser")
		openBrowser(chatURL)
		return
	}

	openChatAppWindow(chatURL)
}

// findAppBrowser returns the full path to an Edge or Chrome executable that
// supports the --app= flag for frameless app-mode windows.  It checks the
// bare command name first (covers machines where the browser is on PATH), then
// falls back to the well-known per-machine and per-user install directories.
// Returns "" when no supported browser is found.
func findAppBrowser() string {
	// Candidates in preference order: Edge first, then Chrome.
	candidates := []string{"msedge", "chrome"}

	// Well-known install-directory suffixes, tried under each of the relevant
	// environment-variable roots below.
	relPaths := []string{
		`Microsoft\Edge\Application\msedge.exe`,
		`Google\Chrome\Application\chrome.exe`,
	}

	// Roots to search for per-machine and per-user installs.
	envRoots := []string{
		os.Getenv("ProgramFiles(x86)"),
		os.Getenv("ProgramFiles"),
		os.Getenv("LocalAppData"),
	}

	// 1. Check whether the bare name is already on PATH.
	for _, name := range candidates {
		if p, err := exec.LookPath(name); err == nil {
			return p
		}
	}

	// 2. Walk the known install roots.
	for _, root := range envRoots {
		if root == "" {
			continue
		}
		for _, rel := range relPaths {
			p := filepath.Join(root, rel)
			if _, err := os.Stat(p); err == nil {
				return p
			}
		}
	}

	return ""
}

func openChatAppWindow(chatURL string) {
	mode := chatClientMode()

	// Tier 1: dedicated Electron chat shell.
	if mode != "browser" {
		if openWithChatShell(chatURL) {
			return
		}
		if mode == "shell" {
			logger.Warn("openChatAppWindow: chat shell not found and mode=shell; cannot open chat")
			return
		}
	}

	// Tier 2: Edge or Chrome in --app= mode with an isolated profile.
	if mode != "browser" {
		browserPath := findAppBrowser()
		if browserPath != "" {
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

			cmd := exec.Command(browserPath, edgeArgs...)
			cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000 /* CREATE_NO_WINDOW */}
			if err := cmd.Start(); err == nil {
				logger.Info("openChatAppWindow: launched browser app-mode (pid=%d)", cmd.Process.Pid)
				return
			}
			logger.Warn("openChatAppWindow: app-mode launch failed, falling back to browser")
		} else {
			logger.Warn("openChatAppWindow: no Edge/Chrome found, falling back to browser")
		}
	}

	// Tier 3: default system browser.
	openBrowser(chatURL)
}

func showOSNotification(title, body string) {
	fmt.Printf("[notification] %s: %s\n", title, body)
}
