//go:build darwin

package config

import "os"

const macOSEnvFile = "/Library/Preferences/io.myportal.tray.env"

func loadMacOS() (*Config, error) {
	f, err := os.Open(macOSEnvFile)
	if err != nil {
		return loadEnvFallback(), nil
	}
	defer f.Close()

	cfg, err := parseEnvConfig(f)
	if err != nil {
		return nil, err
	}
	return cfg, nil
}

func loadWindows() (*Config, error) {
	return loadEnvFallback(), nil
}
