//go:build !windows && !darwin

package config

// loadWindows is a stub for non-Windows platforms.
func loadWindows() (*Config, error) {
	return loadEnvFallback(), nil
}

// loadMacOS is a stub for non-macOS platforms.
func loadMacOS() (*Config, error) {
	return loadEnvFallback(), nil
}
