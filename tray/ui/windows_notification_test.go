//go:build windows

package main

import (
	"encoding/base64"
	"strings"
	"testing"
	"unicode/utf16"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestWindowsToastEncodedCommandAppendsTextNodes(t *testing.T) {
	prevConfig := gConfig
	prevPortalURL := gPortalURL
	gConfig = &api.ConfigResponse{BrandingDisplayName: "Acme Support"}
	gPortalURL = "https://portal.example.test"
	t.Cleanup(func() {
		gConfig = prevConfig
		gPortalURL = prevPortalURL
	})

	cmd := windowsToastEncodedCommand("Script scheduled", `The requested automation has been scheduled and will run in the background shortly.`)
	raw, err := base64.StdEncoding.DecodeString(cmd)
	if err != nil {
		t.Fatalf("decode encoded command: %v", err)
	}
	units := make([]uint16, 0, len(raw)/2)
	for i := 0; i+1 < len(raw); i += 2 {
		units = append(units, uint16(raw[i])|uint16(raw[i+1])<<8)
	}
	script := string(utf16.Decode(units))
	for _, want := range []string{
		"$textNodes = $xml.GetElementsByTagName('text')",
		"$appLogo.SetAttribute('placement', 'appLogoOverride')",
		"$appLogo.SetAttribute('src', 'https://portal.example.test/tray/icon.ico')",
		"AppendChild($xml.CreateTextNode('Script scheduled'))",
		"AppendChild($xml.CreateTextNode('The requested automation has been scheduled and will run in the background shortly.'))",
		"CreateToastNotifier('Acme Support').Show",
	} {
		if !strings.Contains(script, want) {
			t.Fatalf("encoded script missing %q:\n%s", want, script)
		}
	}
	if strings.Contains(script, ".InnerText =") {
		t.Fatalf("encoded script should not use InnerText assignment:\n%s", script)
	}
}
