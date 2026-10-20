package main

import (
	"os"
	"testing"
)

func TestPersistedStateNeedsRewrite(t *testing.T) {
	const portal = "https://portal.example"
	cases := []struct {
		name   string
		state  persistedState
		portal string
		mode   os.FileMode
		goos   string
		want   bool
	}{
		{"missing portal URL from an older release", persistedState{AuthToken: "t"}, portal, 0644, "darwin", true},
		{"missing portal URL on Windows", persistedState{AuthToken: "t"}, portal, 0600, "windows", true},
		{"portal URL changed in install settings", persistedState{PortalURL: "https://old.example"}, portal, 0644, "darwin", true},
		{"owner-only state on macOS", persistedState{PortalURL: portal}, portal, 0600, "darwin", true},
		{"up to date on macOS", persistedState{PortalURL: portal}, portal, 0644, "darwin", false},
		{"up to date on Windows", persistedState{PortalURL: portal}, portal, 0600, "windows", false},
		{"no install URL keeps the enrolled one", persistedState{PortalURL: portal}, "", 0600, "windows", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := tc.state
			if got := persistedStateNeedsRewrite(&s, tc.portal, tc.mode, tc.goos); got != tc.want {
				t.Fatalf("persistedStateNeedsRewrite = %t, want %t", got, tc.want)
			}
		})
	}
}
