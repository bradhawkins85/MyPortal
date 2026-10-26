//go:build !windows

package main

// systemd and launchd already restart the service; Safe Mode is Windows only.
func configureServiceResilience(name string) error { return nil }

func removeSafeBootRegistration(name string) error { return nil }
