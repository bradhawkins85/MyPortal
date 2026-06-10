package main

import (
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/updater"
)

const defaultTrayTooltip = "MyPortal Helpdesk"

func trayTooltipVersion(version string) string {
	version = strings.TrimSpace(version)
	if version == "" {
		return ""
	}
	if strings.HasPrefix(strings.ToLower(version), "v") {
		return version
	}
	return "v" + version
}

// trayTooltip returns the system-tray hover text with the running app version.
func trayTooltip(cfg *api.ConfigResponse) string {
	displayName := defaultTrayTooltip
	if cfg != nil {
		if brandedName := strings.TrimSpace(cfg.BrandingDisplayName); brandedName != "" {
			displayName = brandedName
		}
	}
	version := trayTooltipVersion(updater.AgentVersion)
	if version == "" {
		return displayName
	}
	return displayName + " " + version
}

// versionMenuLabel returns the visible label for an app_version menu node.
func versionMenuLabel(node api.MenuNode) string {
	prefix := strings.TrimSpace(node.Label)
	if prefix == "" {
		prefix = "Version"
	}
	version := strings.TrimSpace(updater.AgentVersion)
	if version == "" {
		version = "unknown"
	}
	return prefix + " " + version
}
