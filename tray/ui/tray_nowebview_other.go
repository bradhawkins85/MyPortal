//go:build nowebview && !windows

// tray_nowebview_other.go provides the headless runUI for non-Windows platforms
// in the CGO=0 (nowebview) build.  A system-tray icon on macOS/Linux requires
// CGO (Cocoa / GTK), so we simply block here; the process is still functional
// for IPC and browser-open operations.
package main

import "github.com/bradhawkins85/myportal-tray/internal/logger"

// runUI blocks until the process is terminated by the service.
func runUI() {
	logger.Info("MyPortal Tray UI (headless/nowebview) running")
	select {}
}
