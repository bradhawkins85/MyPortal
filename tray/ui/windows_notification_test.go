//go:build windows

package main

import (
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestWindowsNotificationUsesBurntToastWithMessageBoxFallback(t *testing.T) {
	if windowsToastAppID != "MyPortal.Tray" {
		t.Fatalf("unexpected app id %q", windowsToastAppID)
	}
	if procMessageBox == nil {
		t.Fatal("MessageBoxW proc was not initialised")
	}
	script := burntToastPowerShellCommand()
	for _, want := range []string{
		"Get-Module -ListAvailable -Name BurntToast",
		"Import-Module BurntToast",
		"New-BurntToastNotification -Text @($Title, $Body)",
	} {
		if !strings.Contains(script, want) {
			t.Fatalf("BurntToast command missing %q: %s", want, script)
		}
	}
	for _, blocked := range []string{"EncodedCommand", "ExecutionPolicy", "Bypass", "CreateShortcut", ".lnk"} {
		if strings.Contains(script, blocked) {
			t.Fatalf("BurntToast command contains blocked pattern %q: %s", blocked, script)
		}
	}
}

func TestWindowsChatNotificationMessageMentionsTrayWhenURLProvided(t *testing.T) {
	prevConfig := gConfig
	gConfig = &api.ConfigResponse{BrandingDisplayName: "Acme Support"}
	t.Cleanup(func() { gConfig = prevConfig })

	message := windowsChatNotificationMessage("New chat", "https://portal.example.test/tray/chat?token=abc")
	for _, want := range []string{"New chat", "Open MyPortal Tray to view the chat."} {
		if !strings.Contains(message, want) {
			t.Fatalf("message missing %q: %q", want, message)
		}
	}
}
