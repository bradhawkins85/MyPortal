package main

import (
	"encoding/json"
	"os"
	"path/filepath"

	"github.com/bradhawkins85/myportal-tray/internal/config"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// refreshPortalURL re-reads the portal URL from the highest-precedence
// source available, in this order:
//  1. MYPORTAL_URL environment variable (always wins; never overwritten).
//  2. tray-state.json (written by the service after a successful enrolment).
//  3. Platform install configuration: /Library/Preferences/io.myportal.tray.env
//     on macOS, or HKLM\Software\MyPortal\Tray\PortalURL on Windows. This
//     lets the tray contact the server on first launch, before the service has
//     finished enrolling the device.
//
// Called both at startup and whenever a config_changed IPC message arrives.
func refreshPortalURL() {
	if envURL := os.Getenv("MYPORTAL_URL"); envURL != "" {
		gPortalURL = envURL
		logger.Debug("refreshPortalURL: using MYPORTAL_URL env (%s)", envURL)
		return
	}
	if state, p := readSharedState(); state.PortalURL != "" {
		gPortalURL = state.PortalURL
		logger.Debug("refreshPortalURL: using portal_url from %s (%s)", p, state.PortalURL)
		return
	}
	if installCfg, cfgErr := config.Load(); cfgErr != nil {
		logger.Warn("refreshPortalURL: install config not readable (%v)", cfgErr)
	} else if installCfg.PortalURL != "" {
		gPortalURL = installCfg.PortalURL
		logger.Debug("refreshPortalURL: using portal URL from install config (%s)", installCfg.PortalURL)
		return
	}
	if regURL := portalURLFromRegistry(); regURL != "" {
		gPortalURL = regURL
		logger.Debug("refreshPortalURL: using PortalURL from registry (%s)", regURL)
		return
	}
	logger.Debug("refreshPortalURL: no portal URL available from any source")
}

// refreshDeviceUID reads the device UID from the platform-appropriate source.
// On Windows it first checks HKLM\Software\MyPortal\Tray\DeviceUID, which is
// written by the tray service after enrolment, then falls back to tray-state.json.
func refreshDeviceUID() {
	if uid := deviceUIDFromRegistry(); uid != "" {
		gDeviceUID = uid
		logger.Debug("refreshDeviceUID: using DeviceUID from registry (%s)", uid)
		return
	}
	if state, p := readSharedState(); state.DeviceUID != "" {
		gDeviceUID = state.DeviceUID
		logger.Debug("refreshDeviceUID: using device_uid from %s (%s)", p, state.DeviceUID)
		return
	}
	logger.Debug("refreshDeviceUID: device UID not available yet")
}

// refreshAuthToken loads the token the UI agent uses with the portal when
// requesting chat and ticket-form URLs, Outlook signatures and menu scripts.
//
// The service gives the UI its own low-privilege token in
// tray-ui-state.json; the device token in tray-state.json is readable by the
// service alone. A service from an older release (or one whose portal cannot
// issue UI tokens yet) still leaves tray-state.json readable, so the device
// token is used then.
func refreshAuthToken() {
	state, p := readSharedState()
	if state.UIToken != "" {
		gAuthToken = state.UIToken
		logger.Debug("refreshAuthToken: UI token loaded from %s", p)
		return
	}
	if state.AuthToken != "" {
		gAuthToken = state.AuthToken
		logger.Debug("refreshAuthToken: device token loaded from %s (service has not issued a UI token)", p)
		return
	}
	logger.Debug("refreshAuthToken: auth token not available yet")
}

// sharedState is what the UI may read from the service's state folder.
type sharedState struct {
	DeviceUID string `json:"device_uid"`
	PortalURL string `json:"portal_url"`
	UIToken   string `json:"ui_token"`
	AuthToken string `json:"auth_token"`
}

// readSharedState returns tray-ui-state.json when the service has written
// it, otherwise the legacy tray-state.json, with the path it read.
func readSharedState() (sharedState, string) {
	return readSharedStateFrom(stateDir())
}

func readSharedStateFrom(dir string) (sharedState, string) {
	for _, name := range []string{"tray-ui-state.json", "tray-state.json"} {
		p := filepath.Join(dir, name)
		data, err := os.ReadFile(p)
		if err != nil {
			logger.Debug("readSharedState: %s not readable (%v)", p, err)
			continue
		}
		var state sharedState
		if err := json.Unmarshal(data, &state); err != nil {
			logger.Warn("Failed to parse %s: %v", p, err)
			continue
		}
		if name == "tray-ui-state.json" {
			// Only the UI token belongs here; never take a device token from it.
			state.AuthToken = ""
			if state.UIToken == "" {
				continue
			}
		}
		return state, p
	}
	return sharedState{}, ""
}
