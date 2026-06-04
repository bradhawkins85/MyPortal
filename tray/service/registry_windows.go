//go:build windows

package main

import (
	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"golang.org/x/sys/windows/registry"
)

const trayRegPath = `Software\MyPortal\Tray`

// saveDeviceUIDToRegistry writes the enrolled device UID to
// HKLM\Software\MyPortal\Tray\DeviceUID so that external apps (and the
// tray UI agent) can read it without needing access to tray-state.json.
func saveDeviceUIDToRegistry(uid string) {
	if uid == "" {
		return
	}
	k, _, err := registry.CreateKey(
		registry.LOCAL_MACHINE,
		trayRegPath,
		registry.SET_VALUE,
	)
	if err != nil {
		logger.Warn("saveDeviceUIDToRegistry: CreateKey: %v", err)
		return
	}
	defer k.Close()
	if err := k.SetStringValue("DeviceUID", uid); err != nil {
		logger.Warn("saveDeviceUIDToRegistry: SetStringValue: %v", err)
		return
	}
	logger.Debug("saveDeviceUIDToRegistry: wrote DeviceUID=%s", uid)
}
