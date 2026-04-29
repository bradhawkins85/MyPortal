//go:build windows

package config

import (
	"golang.org/x/sys/windows/registry"
	"strings"
)

const regKeyPath = `Software\MyPortal\Tray`

func loadWindows() (*Config, error) {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, regKeyPath, registry.QUERY_VALUE)
	if err != nil {
		// Fall back to env vars if registry key doesn't exist yet.
		return loadEnvFallback(), nil
	}
	defer k.Close()

	portalURL, _, _ := k.GetStringValue("PortalURL")
	enrolToken, _, _ := k.GetStringValue("EnrolToken")
	autoUpdateRaw, _, _ := k.GetStringValue("AutoUpdate")
	autoUpdate := strings.ToLower(autoUpdateRaw) != "false"

	return &Config{
		PortalURL:  portalURL,
		EnrolToken: enrolToken,
		AutoUpdate: autoUpdate,
	}, nil
}

func loadMacOS() (*Config, error) {
	return loadEnvFallback(), nil
}
