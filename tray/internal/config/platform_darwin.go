//go:build darwin

package config

import (
	"bufio"
	"os"
	"strings"
)

const macOSEnvFile = "/Library/Preferences/io.myportal.tray.env"

func loadMacOS() (*Config, error) {
	f, err := os.Open(macOSEnvFile)
	if err != nil {
		return loadEnvFallback(), nil
	}
	defer f.Close()

	vals := make(map[string]string)
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) == 2 {
			vals[strings.TrimSpace(parts[0])] = strings.TrimSpace(parts[1])
		}
	}

	au := true
	if strings.ToLower(vals["AUTO_UPDATE"]) == "false" {
		au = false
	}
	return &Config{
		PortalURL:  vals["MYPORTAL_URL"],
		EnrolToken: vals["ENROL_TOKEN"],
		AutoUpdate: au,
	}, nil
}

func loadWindows() (*Config, error) {
	return loadEnvFallback(), nil
}
