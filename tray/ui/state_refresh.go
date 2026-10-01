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
	p := filepath.Join(stateDir(), "tray-state.json")
	data, err := os.ReadFile(p)
	if err == nil {
		var state struct {
			PortalURL string `json:"portal_url"`
		}
		if jsonErr := json.Unmarshal(data, &state); jsonErr != nil {
			logger.Warn("Failed to parse tray-state.json: %v", jsonErr)
		} else if state.PortalURL != "" {
			gPortalURL = state.PortalURL
			logger.Debug("refreshPortalURL: using portal_url from %s (%s)", p, state.PortalURL)
			return
		}
	} else {
		logger.Debug("refreshPortalURL: %s not readable yet (%v)", p, err)
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
	p := filepath.Join(stateDir(), "tray-state.json")
	data, err := os.ReadFile(p)
	if err == nil {
		var state struct {
			DeviceUID string `json:"device_uid"`
		}
		if jsonErr := json.Unmarshal(data, &state); jsonErr == nil && state.DeviceUID != "" {
			gDeviceUID = state.DeviceUID
			logger.Debug("refreshDeviceUID: using device_uid from %s (%s)", p, state.DeviceUID)
			return
		}
	}
	logger.Debug("refreshDeviceUID: device UID not available yet")
}

// refreshAuthToken reads the device auth token from tray-state.json so the UI
// agent can authenticate with the portal when requesting chat tokens, ticket
// questions, and ticket submission.
func refreshAuthToken() {
	p := filepath.Join(stateDir(), "tray-state.json")
	data, err := os.ReadFile(p)
	if err != nil {
		logger.Debug("refreshAuthToken: %s not readable (%v)", p, err)
		return
	}
	var state struct {
		AuthToken string `json:"auth_token"`
	}
	if jsonErr := json.Unmarshal(data, &state); jsonErr == nil && state.AuthToken != "" {
		gAuthToken = state.AuthToken
		logger.Debug("refreshAuthToken: auth token loaded from %s", p)
		return
	}
	logger.Debug("refreshAuthToken: auth token not available yet")
}
