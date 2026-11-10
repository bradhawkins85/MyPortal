package main

import (
	"os"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/secdir"
)

func TestPersistedStateNeedsRewrite(t *testing.T) {
	const portal = "https://portal.example"
	cases := []struct {
		name    string
		state   persistedState
		portal  string
		mode    os.FileMode
		goos    string
		uiReady bool
		want    bool
	}{
		{"missing portal URL from an older release", persistedState{AuthToken: "t"}, portal, 0644, "darwin", false, true},
		{"missing portal URL on Windows", persistedState{AuthToken: "t"}, portal, 0600, "windows", false, true},
		{"portal URL changed in install settings", persistedState{PortalURL: "https://old.example"}, portal, 0644, "darwin", false, true},
		{"owner-only state on macOS without a UI token", persistedState{PortalURL: portal}, portal, 0600, "darwin", false, true},
		{"shared state on macOS without a UI token", persistedState{PortalURL: portal}, portal, 0644, "darwin", false, false},
		{"shared state on macOS once the UI has its own token", persistedState{PortalURL: portal}, portal, 0644, "darwin", true, true},
		{"private state on macOS once the UI has its own token", persistedState{PortalURL: portal}, portal, 0600, "darwin", true, false},
		{"up to date on Windows", persistedState{PortalURL: portal}, portal, 0600, "windows", false, false},
		{"no install URL keeps the enrolled one", persistedState{PortalURL: portal}, "", 0600, "windows", false, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := tc.state
			if got := persistedStateNeedsRewrite(&s, tc.portal, tc.mode, tc.goos, tc.uiReady); got != tc.want {
				t.Fatalf("persistedStateNeedsRewrite = %t, want %t", got, tc.want)
			}
		})
	}
}

func TestDeviceTokenIsPrivateOnceTheUIHasItsOwnToken(t *testing.T) {
	for _, goos := range []string{"windows", "darwin", "linux"} {
		if got := deviceStateAccess(goos, true); got != secdir.Private {
			t.Fatalf("%s: device state access with a UI token = %v, want private", goos, got)
		}
	}
	if deviceStateAccess("windows", false) != secdir.UsersRead || deviceStateAccess("darwin", false) != secdir.UsersRead {
		t.Fatal("without a UI token, the UI must still be able to read the device token")
	}
}
