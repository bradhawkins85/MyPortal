package main

import "testing"

func TestIsSafeBrowserURL(t *testing.T) {
	allowed := []string{
		"https://portal.example.com/tickets/new",
		"https://portal.example.com/chat?room=1&token=abc",
		"http://intranet.local/wiki",
		"HTTPS://Portal.Example.com/",
		"mailto:help@example.com",
	}
	for _, raw := range allowed {
		if !isSafeBrowserURL(raw) {
			t.Errorf("isSafeBrowserURL(%q) = false, want true", raw)
		}
	}
	refused := []string{
		"",
		" https://portal.example.com/",
		"file:///C:/Windows/System32/calc.exe",
		`\\attacker\share\run.exe`,
		"C:\\Windows\\System32\\calc.exe",
		"calc.exe",
		"ms-msdt:/id PCWDiagnostic",
		"search-ms:query=x",
		"javascript:alert(1)",
		"https://x/\"&calc",
		"https://x/ &calc",
		"https://x/\r\ncalc",
		"https:///no-host",
		"https:opaque",
		"-a Calculator",
		"mailto:",
	}
	for _, raw := range refused {
		if isSafeBrowserURL(raw) {
			t.Errorf("isSafeBrowserURL(%q) = true, want false", raw)
		}
	}
}
