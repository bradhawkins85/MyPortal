//go:build windows

package config

import (
	"strings"

	"golang.org/x/sys/windows/registry"
)

const regKeyPath = `Software\MyPortal\Tray`

func loadWindows() (*Config, error) {
	for _, viewFlag := range []uint32{0, registry.WOW64_64KEY, registry.WOW64_32KEY} {
		cfg, ok := loadWindowsFromView(viewFlag)
		if !ok {
			continue
		}
		return cfg, nil
	}
	// Fall back to env vars if registry key doesn't exist yet.
	return loadEnvFallback(), nil
}

func loadMacOS() (*Config, error) {
	return loadEnvFallback(), nil
}

func loadWindowsFromView(viewFlag uint32) (*Config, bool) {
	k, err := registry.OpenKey(
		registry.LOCAL_MACHINE,
		regKeyPath,
		registry.QUERY_VALUE|viewFlag,
	)
	if err != nil {
		return nil, false
	}
	defer k.Close()

	// Legacy compatibility: older installers/scripts used env-style value
	// names, so we read both canonical and legacy keys.
	portalURL := firstRegistryString(k, "PortalURL", "MYPORTAL_URL")
	enrolToken := firstRegistryString(k, "EnrolToken", "ENROL_TOKEN", "ENROLL_TOKEN")
	autoUpdateRaw := firstRegistryString(k, "AutoUpdate", "AUTO_UPDATE")
	autoUpdate := strings.ToLower(autoUpdateRaw) != "false"

	return &Config{
		PortalURL:  portalURL,
		EnrolToken: enrolToken,
		AutoUpdate: autoUpdate,
	}, true
}

func firstRegistryString(k registry.Key, names ...string) string {
	for _, name := range names {
		value, _, err := k.GetStringValue(name)
		if err != nil {
			continue
		}
		value = strings.TrimSpace(value)
		value = strings.Trim(value, `"'`)
		if value != "" {
			return value
		}
	}
	return ""
}
