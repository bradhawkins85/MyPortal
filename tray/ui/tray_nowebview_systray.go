//go:build nowebview && (windows || linux)

// tray_nowebview_systray.go wires up a real system-tray icon for the CGO=0
// (nowebview) build on Windows and Linux desktops. internal/systray uses
// pure-Go Win32 syscalls on Windows and D-Bus on Linux, so CGO is not required.
package main

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"sync/atomic"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"github.com/bradhawkins85/myportal-tray/internal/systray"
)

// lastRestartAt is used to prevent rapid restart loops when config_changed
// events arrive in quick succession.
var lastRestartAt time.Time

// trayReady is set after systray has created its hidden window and notify icon.
// IPC can connect very quickly during startup, and the service replays a
// config_changed message on connect. Calling systray.SetTooltip/SetIcon before
// onTrayReady runs dereferences systray's internal notify-icon state and exits
// the GUI-subsystem process before any error can be shown.
var trayReady atomic.Bool

// hasHandledInitialConfigChanged prevents restart loops from the service's
// IPC onConnect bootstrap replay of config_changed.
var hasHandledInitialConfigChanged bool

// runUI starts the systray event loop; it blocks until systray.Quit() is called.
func runUI() {
	logger.Info("MyPortal Tray UI (%s) starting systray", runtime.GOOS)

	// Wire up the config-changed callback before the event loop starts so
	// that any IPC message arriving during startup is handled correctly.
	onConfigChanged = handleConfigChanged

	systray.Run(onTrayReady, onTrayExit)
}

func onTrayReady() {
	trayReady.Store(true)
	systray.SetTitle("MyPortal")
	systray.SetTooltip(trayTooltip(gConfig))
	// Set the default icon immediately so the tray appears without delay,
	// then fetch the branded icon from the portal in the background.
	systray.SetIcon(defaultTrayIcon())
	buildMenu(gConfig)
	go fetchAndSetIcon()
}

// fetchAndSetIcon downloads the device's branding icon, or failing that the
// portal-wide .ico, and applies it to the running tray icon.  It is safe to
// call from any goroutine.
func fetchAndSetIcon() {
	if gPortalURL == "" {
		logger.Debug("fetchAndSetIcon: skipped — gPortalURL is empty (will retry on next config_changed)")
		return
	}
	data, url, err := fetchFirstTrayIcon(trayIconURLs(gPortalURL, gConfig), downloadTrayIcon, trayIconForPlatform)
	if err != nil {
		logger.Warn("Tray icon fetch failed, keeping current icon: %v", err)
		return
	}
	systray.SetIcon(data)
	logger.Info("Tray icon updated from %s", url)
}

func downloadTrayIcon(url string) ([]byte, error) {
	logger.Debug("fetchAndSetIcon: GET %s", url)
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
	if err != nil {
		return nil, err
	}
	logger.Debug("fetchAndSetIcon: received %d bytes from %s (content-type=%q)",
		len(data), url, resp.Header.Get("Content-Type"))
	return data, nil
}

// handleConfigChanged is called when the service broadcasts a config_changed
// IPC message.  It refreshes the portal icon immediately and schedules a
// process restart so the tray menu is rebuilt from the updated config.
// A 60-second cooldown prevents restart storms if multiple config_changed
// events arrive in quick succession.
func handleConfigChanged(cfg *api.ConfigResponse) {
	if !trayReady.Load() {
		// The initial config replay can arrive before systray.Run has completed
		// native initialization. gConfig has already been refreshed by the IPC
		// handler, so onTrayReady will render the latest cache shortly.
		if !hasHandledInitialConfigChanged {
			hasHandledInitialConfigChanged = true
		}
		logger.Debug("config_changed: received before tray ready; deferring systray updates")
		return
	}

	systray.SetTooltip(trayTooltip(cfg))

	// Always refresh the icon — this can be updated without a restart.
	go fetchAndSetIcon()

	// The service replays one config_changed event on initial IPC connect so
	// late-starting UI agents can bootstrap state. Skip restart for that first
	// event to avoid self-restart loops.
	if !hasHandledInitialConfigChanged {
		hasHandledInitialConfigChanged = true
		logger.Debug("config_changed: initial bootstrap event received; restart skipped")
		return
	}

	// Restart the process to rebuild the menu from the fresh config cache.
	if time.Since(lastRestartAt) < 60*time.Second {
		logger.Info("config_changed: skipping restart (cooldown active)")
		return
	}
	lastRestartAt = time.Now()
	go func() {
		// The service writes the config to disk before broadcasting
		// config_changed, so the updated file is already present.  No
		// sleep is required — we can start the replacement process
		// immediately.  cmd.Start() is called before systray.Quit() so
		// that the new process is guaranteed to be running before the
		// current one terminates (Quit may unblock main() very quickly).
		exe, err := os.Executable()
		if err != nil {
			logger.Warn("config_changed restart: os.Executable: %v", err)
			return
		}
		cmd := exec.Command(exe, os.Args[1:]...)
		cmd.Env = append(os.Environ(), "MYPORTAL_TRAY_RESTART=1")
		if startErr := cmd.Start(); startErr != nil {
			logger.Warn("config_changed restart: Start: %v", startErr)
			return
		}
		logger.Info("config_changed: restarting UI to rebuild menu")
		systray.Quit()
	}()
}

func onTrayExit() {
	trayReady.Store(false)
	if gIPCConn != nil {
		gIPCConn.Close()
	}
}

// buildMenu constructs the systray menu from a ConfigResponse.
// Note: getlantern/systray v1.2.2 does not expose a ResetMenu API, so this
// function is only called once (on startup). Dynamic menu rebuilding is
// handled by restarting the UI process when a config_changed event arrives.
func buildMenu(cfg *api.ConfigResponse) {
	if cfg == nil {
		cfg = defaultConfig()
	}
	logger.Debug("buildMenu: rendering version=%d, %d top-level node(s), chat_enabled=%t",
		cfg.Version, len(cfg.Menu), cfg.ChatEnabled)
	for _, node := range cfg.Menu {
		addNode(node, cfg, nil)
	}
}

func addTrayMenuItem(parent *systray.MenuItem, label string, tooltip string) *systray.MenuItem {
	if parent != nil {
		return parent.AddSubMenuItem(label, tooltip)
	}
	return systray.AddMenuItem(label, tooltip)
}

func addTraySeparator(parent *systray.MenuItem) {
	if parent == nil {
		systray.AddSeparator()
		return
	}
	item := parent.AddSubMenuItem("────────", "")
	item.Disable()
}

func addNode(node api.MenuNode, cfg *api.ConfigResponse, parent *systray.MenuItem) {
	// Menu type discriminators come from administrator-authored JSON. Normalise
	// them before dispatch so mixed-case values such as TRMM_Script always get
	// their click handler attached.
	nodeType := normalizedMenuNodeType(node.Type)
	switch nodeType {
	case "separator":
		addTraySeparator(parent)

	case "label":
		item := addTrayMenuItem(parent, node.Label, "")
		if node.Color == "" {
			item.Disable()
		}

	case "app_version":
		item := addTrayMenuItem(parent, versionMenuLabel(node), "")
		item.Disable()

	case "link":
		item := addTrayMenuItem(parent, node.Label, node.URL)
		go func(url string) {
			for range item.ClickedCh {
				openBrowser(url)
			}
		}(node.URL)

	case "open_chat":
		if !cfg.ChatEnabled {
			return
		}
		label := node.Label
		if label == "" {
			label = "Chat with helpdesk"
		}
		item := addTrayMenuItem(parent, label, "Open chat window")
		go func() {
			for range item.ClickedCh {
				openChatWindow("", cfg)
			}
		}()

	case "display_text":
		label := node.Label
		if label == "" {
			label = "Info"
		}
		item := addTrayMenuItem(parent, label, "")
		go func(text string) {
			for range item.ClickedCh {
				showTextWindow("Information", text)
			}
		}(cfg.DisplayText)

	case "env_var":
		label := resolveEnvVarMenuLabel(node)
		item := addTrayMenuItem(parent, label, "Click to copy value")
		go func(varName string) {
			for range item.ClickedCh {
				val := resolveEnvVarValue(varName)
				if val == "" {
					val = "(not set)"
				}
				showTextWindow(varName, val)
			}
		}(normalizeEnvVarName(node.Name))

	case "submenu":
		label := node.Label
		if label == "" {
			label = "Menu"
		}
		item := addTrayMenuItem(parent, label, "")
		for _, child := range node.Children {
			if child == nil {
				continue
			}
			addNode(*child, cfg, item)
		}

	case "submit_ticket":
		label := node.Label
		if label == "" {
			label = "Submit Ticket"
		}
		item := addTrayMenuItem(parent, label, "Submit a support ticket")
		go func() {
			for range item.ClickedCh {
				go openNewTicketDialog(cfg)
			}
		}()

	case "submit_syncro_ticket":
		label := node.Label
		if label == "" {
			label = "Create Syncro Ticket"
		}
		item := addTrayMenuItem(parent, label, "Create a support ticket in Syncro")
		go func() {
			for range item.ClickedCh {
				go openSyncroTicketDialog(cfg)
			}
		}()

	case "trmm_script":
		label := node.Label
		if label == "" {
			label = node.ScriptName
		}
		if label == "" {
			label = "Run Tactical RMM Script"
		}
		item := addTrayMenuItem(parent, label, "Run a Tactical RMM script on this computer")
		go func(menuNode api.MenuNode) {
			for range item.ClickedCh {
				go runTRMMScriptFromMenu(menuNode)
			}
		}(node)

	case "rmm_script":
		label := node.Label
		if label == "" {
			label = node.ScriptName
		}
		if label == "" {
			label = "Run Script"
		}
		item := addTrayMenuItem(parent, label, "Run a script on this computer")
		go func(menuNode api.MenuNode) {
			for range item.ClickedCh {
				go runRMMScriptFromMenu(menuNode)
			}
		}(node)

	case "scan_network":
		if !cfg.NetworkScannerEnabled {
			return
		}
		label := node.Label
		if label == "" {
			label = "Scan Network"
		}
		item := addTrayMenuItem(parent, label, "Scan the local network now")
		go func() {
			for range item.ClickedCh {
				requestNetworkScan()
			}
		}()

	case "refresh_signatures":
		// The portal only sends this item to companies with Classic Outlook
		// signature templates; Outlook signatures are managed on Windows only.
		if !outlookSignatureRefreshSupported() {
			return
		}
		label := node.Label
		if label == "" {
			label = "Refresh Email Signatures"
		}
		item := addTrayMenuItem(parent, label, "Update your Outlook email signatures now")
		go func() {
			for range item.ClickedCh {
				go refreshOutlookSignaturesFromMenu()
			}
		}()

	case "refresh_config":
		label := node.Label
		if label == "" {
			label = "Refresh"
		}
		item := addTrayMenuItem(parent, label, "Refresh tray menu from server")
		go func() {
			for range item.ClickedCh {
				requestConfigRefresh()
			}
		}()

	case "quit":
		label := node.Label
		if label == "" {
			label = "Quit"
		}
		item := addTrayMenuItem(parent, label, "Exit MyPortal tray")
		go func() {
			for range item.ClickedCh {
				systray.Quit()
			}
		}()

	default:
		logger.Warn("Ignoring unsupported tray menu node type %q (label=%q)", node.Type, node.Label)
	}
}
