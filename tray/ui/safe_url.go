package main

import (
	"net/url"
	"strings"
	"unicode"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// openBrowser opens a web link in the user's default browser.
//
// Links reach the tray from MyPortal (menu items, chat and ticket URLs) and
// from tray-config.json on disk. Only absolute http(s) and mailto links are
// opened: anything else (file:, UNC paths, other protocol handlers, local
// programs) could start a program as the signed-in user, so it is refused.
func openBrowser(raw string) {
	if !isSafeBrowserURL(raw) {
		logger.Warn("openBrowser: refused to open a link that is not an http, https or mailto address")
		return
	}
	launchURL(raw)
}

// isSafeBrowserURL reports whether raw is an absolute http, https or mailto
// URL with no characters a shell or protocol handler could misread.
func isSafeBrowserURL(raw string) bool {
	if raw == "" || len(raw) > 8192 || raw != strings.TrimSpace(raw) {
		return false
	}
	for _, r := range raw {
		if unicode.IsControl(r) || unicode.IsSpace(r) || r == '"' || r == '\\' {
			return false
		}
	}
	u, err := url.Parse(raw)
	if err != nil {
		return false
	}
	switch strings.ToLower(u.Scheme) {
	case "http", "https":
		return u.Host != "" && u.Opaque == ""
	case "mailto":
		return u.Opaque != ""
	default:
		return false
	}
}
