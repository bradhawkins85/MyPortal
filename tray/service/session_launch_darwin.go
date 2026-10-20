//go:build darwin

package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

const (
	trayAgentLabel = "io.myportal.tray.agent"
	trayAgentPlist = "/Library/LaunchAgents/io.myportal.tray.agent.plist"
	launchctlPath  = "/bin/launchctl"

	// The first check waits for the login to settle; later checks catch an
	// agent that launchd never loaded or started.
	trayUIWatchdogFirstCheck = 20 * time.Second
	trayUIWatchdogInterval   = time.Minute
)

// consoleUID returns the uid of the user logged in at the console, or an
// error when only the login window is showing.
func consoleUID() (int, error) {
	var st syscall.Stat_t
	if err := syscall.Stat("/dev/console", &st); err != nil {
		return 0, fmt.Errorf("stat /dev/console: %w", err)
	}
	if st.Uid < 500 {
		return 0, errors.New("no user is logged in at the console")
	}
	return int(st.Uid), nil
}

func launchctl(args ...string) (string, error) {
	out, err := exec.Command(launchctlPath, args...).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

// ensureTrayAgent loads the menu-bar LaunchAgent into the console user's GUI
// domain if launchd does not have it, and starts it if it is loaded but has
// never run. With restartQuit it also restarts an agent the user quit, which
// is wanted when a chat must be shown. It reports whether it changed anything.
func ensureTrayAgent(restartQuit bool) (bool, error) {
	uid, err := consoleUID()
	if err != nil {
		return false, err
	}
	domain := fmt.Sprintf("gui/%d", uid)
	target := domain + "/" + trayAgentLabel

	out, err := launchctl("print", target)
	if err != nil {
		if _, statErr := os.Stat(trayAgentPlist); statErr != nil {
			return false, fmt.Errorf("tray agent plist: %w", statErr)
		}
		// RunAtLoad starts the agent once it is bootstrapped.
		if out, err := launchctl("bootstrap", domain, trayAgentPlist); err != nil {
			return false, fmt.Errorf("launchctl bootstrap %s: %v: %s", domain, err, out)
		}
		logger.Info("Loaded the tray UI agent for console user %d", uid)
		return true, nil
	}

	st := parseLaunchdJobState(out)
	if st.Running || (!st.NeverExited && !restartQuit) {
		return false, nil
	}
	if out, err := launchctl("kickstart", target); err != nil {
		return false, fmt.Errorf("launchctl kickstart %s: %v: %s", target, err, out)
	}
	logger.Info("Started the tray UI agent for console user %d", uid)
	return true, nil
}

func launchTrayUIForActiveUser() error {
	_, err := ensureTrayAgent(true)
	return err
}

// trayUIWatchdogLoop makes sure the menu-bar agent runs for the console user.
// launchd normally starts it at login, but an upgrade whose postinstall could
// not reach the user's GUI domain, or a login where launchd skipped the job,
// otherwise leaves the Mac without a tray icon until the next login.
func (d *daemon) trayUIWatchdogLoop() {
	timer := time.NewTimer(trayUIWatchdogFirstCheck)
	defer timer.Stop()
	lastErr := ""
	for {
		select {
		case <-d.stopCh:
			return
		case <-timer.C:
		}
		if _, err := ensureTrayAgent(false); err != nil {
			// Log each distinct failure once; the console is often just
			// at the login window.
			if msg := err.Error(); msg != lastErr {
				logger.Debug("tray UI watchdog: %v", err)
				lastErr = msg
			}
		} else {
			lastErr = ""
		}
		timer.Reset(trayUIWatchdogInterval)
	}
}
