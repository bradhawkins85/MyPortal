//go:build windows

package config

import (
	"fmt"
	"strings"

	"golang.org/x/sys/windows/registry"
)

const regKeyPath = `Software\MyPortal\Tray`
const legacyRegPath = `Software\WOW6432Node\MyPortal\Tray`

func loadWindows() (*Config, error) {
	for _, viewFlag := range []uint32{registry.WOW64_64KEY, 0} {
		if cfg, ok := loadWindowsFromView(viewFlag); ok {
			return cfg, nil
		}
	}
	if cfg, ok := loadWindowsLegacy(); ok {
		_ = migrateLegacyWindowsRegistry(cfg)
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

		func loadWindowsLegacy() (*Config, bool) {
			k, err := registry.OpenKey(
				registry.LOCAL_MACHINE,
				legacyRegPath,
				registry.QUERY_VALUE|registry.WOW64_64KEY,
			)
			if err != nil {
				return nil, false
			}
			defer k.Close()

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

		func migrateLegacyWindowsRegistry(cfg *Config) error {
			k, _, err := registry.CreateKey(
				registry.LOCAL_MACHINE,
				regKeyPath,
				registry.SET_VALUE|registry.WOW64_64KEY,
			)
			if err != nil {
				return err
			}
			defer k.Close()

			if cfg.PortalURL != "" {
				if err := k.SetStringValue("PortalURL", cfg.PortalURL); err != nil {
					return err
				}
			}
			if cfg.EnrolToken != "" {
				if err := k.SetStringValue("EnrolToken", cfg.EnrolToken); err != nil {
					return err
				}
			}
			autoUpdateValue := "true"
			if !cfg.AutoUpdate {
				autoUpdateValue = "false"
			}
			if err := k.SetStringValue("AutoUpdate", autoUpdateValue); err != nil {
				return err
			}

			software, err := registry.OpenKey(
				registry.LOCAL_MACHINE,
				`Software\WOW6432Node\MyPortal`,
				registry.ALL_ACCESS|registry.WOW64_64KEY,
			)
			if err != nil {
				return nil
			}
			defer software.Close()
			if err := registry.DeleteKey(software, "Tray"); err != nil {
				return fmt.Errorf("delete legacy key: %w", err)
			}
			return nil
		}
		value = strings.TrimSpace(value)
		value = strings.Trim(value, `"'`)
		if value != "" {
			return value
		}
	}
	return ""
}
