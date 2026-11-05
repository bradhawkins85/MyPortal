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
		"Install-Module -Name BurntToast -Scope CurrentUser",
		"Import-Module BurntToast",
		"/tray/icon.png",
		"New-BTButton -Content $ButtonText -Arguments $ButtonUrl",
		"Submit-BTNotification",
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

func TestBurntToastArgsPassValuesAsParameters(t *testing.T) {
	args := burntToastArgs("New MyPortal chat", "Hello", "Open chat", "http://127.0.0.1:1234/open-chat?id=x", "https://portal.example.test", false)
	joined := strings.Join(args, "\x00")
	for _, want := range []string{"-Title\x00New MyPortal chat", "-Message\x00Hello", "-ButtonText\x00Open chat", "-ButtonUrl\x00http://127.0.0.1:1234/open-chat?id=x", "-PortalUrl\x00https://portal.example.test"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("args missing %q: %q", want, args)
		}
	}
	if strings.Contains(joined, "-AllowInstall") {
		t.Fatalf("args should not allow install after a failed install: %q", args)
	}
	if !strings.Contains(strings.Join(burntToastArgs("t", "b", "", "", "", true), " "), "-AllowInstall") {
		t.Fatal("expected -AllowInstall when installs are allowed")
	}
}
