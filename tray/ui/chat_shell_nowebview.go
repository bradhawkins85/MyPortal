//go:build nowebview

// chat_shell_nowebview.go provides cross-platform helpers for locating and
// launching the myportal-tray-chat dedicated chat shell.  The chat shell is a
// minimal Electron application that opens the MyPortal chat page in an
// isolated app window, completely separate from the user's browser sessions.
//
// Launch priority for openChatWindow on every platform:
//   1. Dedicated chat shell (myportal-tray-chat[.exe]) — best isolation.
//   2. Chromium-based browser in --app= mode with --user-data-dir isolation.
//   3. Default system browser — legacy fallback.
//
// The server-side ChatClientMode field can override this priority:
//   ""/"app" (default) — use priority order above.
//   "browser"          — skip to step 3 immediately.
//   "shell"            — use step 1 only; warn if absent instead of falling back.
package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"runtime"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// chatShellBinaryName is the platform-agnostic base name (without .exe) of the
// dedicated chat shell binary that is installed alongside the tray binaries.
const chatShellBinaryName = "myportal-tray-chat"

// fileExists returns true when the file at p is present and readable.
func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// findChatShellInDir returns the full path to the chat shell executable inside
// dir, or "" if it is not present.  Kept as a separate function so tests can
// exercise the discovery logic without relying on the location of the test binary.
//
// Platform details:
//   - Windows:  looks for chatShellBinaryName + ".exe".
//   - macOS:    looks for chatShellBinaryName + ".app/Contents/MacOS/" + chatShellBinaryName
//     (the binary inside the Electron app bundle).
//   - Other:    looks for chatShellBinaryName as a plain file.
func findChatShellInDir(dir string) string {
	var p string
	switch runtime.GOOS {
	case "windows":
		p = filepath.Join(dir, chatShellBinaryName+".exe")
	case "darwin":
		// On macOS the chat shell ships as an Electron .app bundle.  The main
		// executable is the Mach-O binary inside Contents/MacOS/ and can be
		// executed directly (no `open -a` required).
		p = filepath.Join(dir, chatShellBinaryName+".app", "Contents", "MacOS", chatShellBinaryName)
	default:
		p = filepath.Join(dir, chatShellBinaryName)
	}
	if fileExists(p) {
		return p
	}
	return ""
}

// findChatShell returns the absolute path to the myportal-tray-chat binary or
// "" when the shell is not installed.
//
// Search order:
//  1. Same directory as the running tray UI binary (standard install).
//  2. Well-known per-platform install paths as a fallback.
func findChatShell() string {
	// 1. Sibling of own executable (covers standard MSI / .pkg install).
	if self, err := os.Executable(); err == nil {
		if p := findChatShellInDir(filepath.Dir(self)); p != "" {
			logger.Debug("findChatShell: found at %s", p)
			return p
		}
	}

	// 2. Well-known platform-specific install roots.
	switch runtime.GOOS {
	case "windows":
		pf := os.Getenv("ProgramFiles")
		if pf == "" {
			pf = `C:\Program Files`
		}
		if p := findChatShellInDir(filepath.Join(pf, "MyPortalTray")); p != "" {
			logger.Debug("findChatShell: found at %s", p)
			return p
		}
	case "darwin":
		if p := findChatShellInDir("/Library/MyPortal/Tray"); p != "" {
			logger.Debug("findChatShell: found at %s", p)
			return p
		}
	}

	logger.Debug("findChatShell: not found")
	return ""
}

// openWithChatShell launches the dedicated chat shell with chatURL as its
// argument.  Returns true when the shell was found and the process started
// successfully.
func openWithChatShell(chatURL string) bool {
	shellPath := findChatShell()
	if shellPath == "" {
		return false
	}
	cmd := exec.Command(shellPath, "--url="+chatURL)
	if err := cmd.Start(); err != nil {
		logger.Warn("openWithChatShell: launch failed: %v", err)
		return false
	}
	logger.Info("openWithChatShell: launched (pid=%d)", cmd.Process.Pid)
	return true
}

// chatClientMode returns the effective mode from the cached config, defaulting
// to "app" when not set.
func chatClientMode() string {
	if gConfig != nil && gConfig.ChatClientMode != "" {
		return gConfig.ChatClientMode
	}
	return "app"
}
