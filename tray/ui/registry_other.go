//go:build !windows

package main

// portalURLFromRegistry is a no-op on non-Windows platforms; the registry
// fallback only applies to the Windows MSI install path.
func portalURLFromRegistry() string { return "" }

// deviceUIDFromRegistry is a no-op on non-Windows platforms.
func deviceUIDFromRegistry() string { return "" }

// loadTicketPrefill returns empty strings on non-Windows platforms.
func loadTicketPrefill() (name, email, phone string) { return "", "", "" }

// saveTicketPrefill is a no-op on non-Windows platforms.
func saveTicketPrefill(_, _, _ string) {}
