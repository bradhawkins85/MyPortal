//go:build !darwin

package main

// trayUIWatchdogLoop only runs on macOS, where launchd starts the UI agent.
func (d *daemon) trayUIWatchdogLoop() {}
