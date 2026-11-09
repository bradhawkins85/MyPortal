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

// enrolTokenValueNames are every registry value name the tray reads an
// install token from (see internal/config/platform_windows.go).
var enrolTokenValueNames = []string{"EnrolToken", "ENROL_TOKEN", "ENROLL_TOKEN"}

// forgetEnrolToken removes the company install token from HKLM once the
// device holds its own auth token. HKLM\Software is readable by every local
// user, and the install token can enrol new devices into the company.
func forgetEnrolToken() {
	for _, path := range []string{trayRegPath, `Software\WOW6432Node\MyPortal\Tray`} {
		k, err := registry.OpenKey(
			registry.LOCAL_MACHINE,
			path,
			registry.QUERY_VALUE|registry.SET_VALUE|registry.WOW64_64KEY,
		)
		if err != nil {
			continue
		}
		for _, name := range enrolTokenValueNames {
			if _, _, err := k.GetValue(name, nil); err != nil {
				continue
			}
			if err := k.DeleteValue(name); err != nil {
				logger.Warn("forgetEnrolToken: delete %s\\%s: %v", path, name, err)
				continue
			}
			logger.Info("Removed saved install token %s\\%s", path, name)
		}
		k.Close()
	}
}
