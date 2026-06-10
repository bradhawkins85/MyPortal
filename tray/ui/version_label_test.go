package main

import (
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/updater"
)

func TestTrayTooltipIncludesAgentVersion(t *testing.T) {
	old := updater.AgentVersion
	updater.AgentVersion = "9.8.7"
	t.Cleanup(func() { updater.AgentVersion = old })

	got := trayTooltip(nil)
	if !strings.Contains(got, "9.8.7") {
		t.Fatalf("trayTooltip() = %q, want it to include agent version", got)
	}
}

func TestTrayTooltipUsesBrandingDisplayName(t *testing.T) {
	old := updater.AgentVersion
	updater.AgentVersion = "9.8.7"
	t.Cleanup(func() { updater.AgentVersion = old })

	got := trayTooltip(&api.ConfigResponse{BrandingDisplayName: "Acme Support"})
	if !strings.Contains(got, "Acme Support") {
		t.Fatalf("trayTooltip() = %q, want it to include branded display name", got)
	}
}

func TestVersionMenuLabelUsesDefaultPrefix(t *testing.T) {
	old := updater.AgentVersion
	updater.AgentVersion = "1.2.3"
	t.Cleanup(func() { updater.AgentVersion = old })

	got := versionMenuLabel(api.MenuNode{Type: "app_version"})
	if got != "Version 1.2.3" {
		t.Fatalf("versionMenuLabel() = %q, want %q", got, "Version 1.2.3")
	}
}

func TestVersionMenuLabelUsesCustomPrefix(t *testing.T) {
	old := updater.AgentVersion
	updater.AgentVersion = "1.2.3"
	t.Cleanup(func() { updater.AgentVersion = old })

	got := versionMenuLabel(api.MenuNode{Type: "app_version", Label: "Tray app"})
	if got != "Tray app 1.2.3" {
		t.Fatalf("versionMenuLabel() = %q, want %q", got, "Tray app 1.2.3")
	}
}
