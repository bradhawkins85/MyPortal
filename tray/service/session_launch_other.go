//go:build !windows && !darwin

package main

import "fmt"

func launchTrayUIForActiveUser() error {
	// XDG autostart opens the tray UI when a user signs in to a desktop.
	return fmt.Errorf("the tray UI is not running: nobody is signed in to a desktop, or the desktop has no tray")
}
