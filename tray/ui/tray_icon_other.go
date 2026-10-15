//go:build !darwin && !nowebview

package main

import "github.com/getlantern/systray"

func configureTrayIcon() {
	systray.SetTitle("MyPortal")
}

// requestTrayIconRefresh is a no-op: only the macOS webview build loads the
// portal icon.
func requestTrayIconRefresh() {}
