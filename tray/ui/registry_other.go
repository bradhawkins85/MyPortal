//go:build nowebview && !windows

package main

// portalURLFromRegistry is a no-op on non-Windows platforms; the registry
// fallback only applies to the Windows MSI install path.
func portalURLFromRegistry() string { return "" }
