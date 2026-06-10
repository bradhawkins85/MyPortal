package main

import (
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/updater"
)

const defaultTrayTooltip = "MyPortal Helpdesk"

// trayTooltip returns the system-tray hover text with the running app version.
func trayTooltip() string {
	version := strings.TrimSpace(updater.AgentVersion)
	if version == "" {
		return defaultTrayTooltip
	}
	return defaultTrayTooltip + " v" + version
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
