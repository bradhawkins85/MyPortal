//go:build nowebview && darwin

// platform_nowebview_darwin.go provides macOS-specific UI helpers for the
// CGO=0 (nowebview) build.  Chat is opened in a dedicated app window rather
// than the default browser:
//
//  1. Dedicated chat shell (myportal-tray-chat) — best isolation, no shared
//     browser sessions.
//  2. Chromium-based browser in --app= mode with an isolated --user-data-dir.
//  3. Default browser via `open` — emergency fallback only.
//
// The active strategy is controlled by the ChatClientMode field that the
// server includes in the /api/tray/config response.
package main

import (
	"os"
	"os/exec"
	"path/filepath"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

func openBrowser(url string) {
	if err := exec.Command("open", url).Start(); err != nil {
		logger.Warn("openBrowser: failed to open %s: %v", url, err)
	}
}

func showTextWindow(title, text string) {
	// macOS nowebview: log to stdout.  A native dialog via osascript would
	// require quoting the text carefully; that can be a future improvement.
	logger.Info("[%s] %s", title, text)
}

// openChatWindow resolves the chat URL (requesting an auth token when needed)
// then delegates to openChatAppWindow which handles the launch strategy.
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
	if chatURL == "" {
		logger.Warn("openChatWindow: could not determine chat URL")
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

// findAppBrowser returns the full path to a Chromium-based browser binary that
// supports the --app= flag.  Checks standard macOS application bundle paths in
// preference order (Edge > Chrome > Chromium > Brave).  Returns "" when none
// is found.
func findAppBrowser() string {
	// System-wide application bundles.
	candidates := []string{
		"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
		"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
		"/Applications/Chromium.app/Contents/MacOS/Chromium",
		"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
	}

	// Per-user installs (~/<Applications>/…).
	if home, err := os.UserHomeDir(); err == nil {
		userApps := filepath.Join(home, "Applications")
		candidates = append(candidates,
			filepath.Join(userApps, "Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
			filepath.Join(userApps, "Google Chrome.app/Contents/MacOS/Google Chrome"),
			filepath.Join(userApps, "Chromium.app/Contents/MacOS/Chromium"),
		)
	}

	for _, p := range candidates {
		if fileExists(p) {
			return p
		}
	}
	return ""
}

// openChatAppWindow implements the three-tier launch strategy for macOS.
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

	// Tier 2: Chromium app-mode with isolated profile.
	if mode != "browser" {
		if browserPath := findAppBrowser(); browserPath != "" {
			profileDir := filepath.Join(os.TempDir(), "MyPortal", "tray-chat-profile")
			args := []string{
				"--app=" + chatURL,
				"--window-size=920,680",
				"--disable-dev-tools",
				"--disable-extensions",
				"--no-first-run",
				"--no-default-browser-check",
				"--user-data-dir=" + profileDir,
			}
			cmd := exec.Command(browserPath, args...)
			if err := cmd.Start(); err == nil {
				logger.Info("openChatAppWindow: launched browser app-mode (pid=%d)", cmd.Process.Pid)
				return
			}
			logger.Warn("openChatAppWindow: browser app-mode failed, falling back to default browser")
		} else {
			logger.Warn("openChatAppWindow: no Chromium-based browser found, falling back to default browser")
		}
	}

	// Tier 3: default system browser.
	openBrowser(chatURL)
}

func showOSNotification(title, body string) {
	script := `display notification "` + body + `" with title "` + title + `"`
	if err := exec.Command("osascript", "-e", script).Start(); err != nil {
		logger.Debug("showOSNotification: osascript failed: %v", err)
	}
}
