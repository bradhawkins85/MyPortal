//go:build !darwin && !nowebview

package main

import "github.com/getlantern/systray"

func configureTrayIcon() {
	systray.SetTitle("MyPortal")
}
