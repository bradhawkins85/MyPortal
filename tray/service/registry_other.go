//go:build !windows

package main

// saveDeviceUIDToRegistry is a no-op on non-Windows platforms.
func saveDeviceUIDToRegistry(_ string) {}

// forgetEnrolToken is a no-op on non-Windows platforms.
func forgetEnrolToken() {}
