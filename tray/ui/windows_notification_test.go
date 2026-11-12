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

func TestBurntToastPassesValuesOutsideTheScript(t *testing.T) {
	args := burntToastArgs()
	if args[len(args)-1] != burntToastPowerShellCommand() || args[len(args)-2] != "-Command" {
		t.Fatalf("the script must be the last argument: %q", args)
	}
	hostile := "'; Start-Process calc; '"
	for _, arg := range args {
		if strings.Contains(arg, hostile) {
			t.Fatalf("notification text reached the command line: %q", args)
		}
	}
	env := strings.Join(burntToastEnv(hostile, "Hello", "Open chat", "http://127.0.0.1:1234/open-chat?id=x", "https://portal.example.test", false), "\x00")
	for _, want := range []string{
		"MYPORTAL_TOAST_TITLE=" + hostile,
		"MYPORTAL_TOAST_MESSAGE=Hello",
		"MYPORTAL_TOAST_BUTTON_TEXT=Open chat",
		"MYPORTAL_TOAST_BUTTON_URL=http://127.0.0.1:1234/open-chat?id=x",
		"MYPORTAL_TOAST_PORTAL_URL=https://portal.example.test",
		"MYPORTAL_TOAST_ALLOW_INSTALL=\x00",
	} {
		if !strings.Contains(env+"\x00", want) {
			t.Fatalf("env missing %q: %q", want, env)
		}
	}
	if !strings.Contains(strings.Join(burntToastEnv("t", "b", "", "", "", true), " "), "MYPORTAL_TOAST_ALLOW_INSTALL=1") {
		t.Fatal("expected installs to be allowed")
	}
}
