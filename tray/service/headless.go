package main

import (
	"os"
	"path/filepath"
	"runtime"
)

// linuxUIName is the tray UI the Linux installer adds next to the service on
// computers with a desktop. Servers (Proxmox VE and other headless Linux) get
// the service only.
const linuxUIName = "myportal-tray-ui"

// headless reports whether this install has no tray UI: a Linux install
// without the desktop UI. Messages meant for the signed-in user are logged
// instead of shown, and the service opens no local socket for a UI.
func headless() bool {
	return runtime.GOOS == "linux" && !linuxDesktopInstalled()
}

var linuxDesktopInstalled = func() bool {
	exe, err := os.Executable()
	if err != nil {
		return false
	}
	info, err := os.Stat(filepath.Join(filepath.Dir(exe), linuxUIName))
	return err == nil && info.Mode().IsRegular()
}

// uiReadsState reports whether a tray UI running as the signed-in user must
// read tray-state.json (macOS, and Linux desktops).
func uiReadsState(goos string) bool {
	return goos == "darwin" || (goos == "linux" && !headless())
}
