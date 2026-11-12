package main

import (
	"os"
	"path/filepath"
	"testing"
)

func writeStateFile(t *testing.T, dir, name, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func TestSharedStatePrefersTheUIToken(t *testing.T) {
	dir := t.TempDir()
	writeStateFile(t, dir, "tray-ui-state.json", `{"device_uid":"dev","portal_url":"https://portal","ui_token":"ui-only"}`)
	writeStateFile(t, dir, "tray-state.json", `{"device_uid":"dev","portal_url":"https://portal","auth_token":"device"}`)

	state, path := readSharedStateFrom(dir)
	if state.UIToken != "ui-only" || state.AuthToken != "" {
		t.Fatalf("state = %#v, want only the UI token", state)
	}
	if filepath.Base(path) != "tray-ui-state.json" || state.PortalURL != "https://portal" || state.DeviceUID != "dev" {
		t.Fatalf("read %s: %#v", path, state)
	}
}

func TestSharedStateNeverTakesADeviceTokenFromTheUIFile(t *testing.T) {
	dir := t.TempDir()
	writeStateFile(t, dir, "tray-ui-state.json", `{"ui_token":"ui-only","auth_token":"planted"}`)

	state, _ := readSharedStateFrom(dir)
	if state.AuthToken != "" || state.UIToken != "ui-only" {
		t.Fatalf("state = %#v", state)
	}
}

func TestSharedStateFallsBackToTheLegacyFileFromAnOlderService(t *testing.T) {
	dir := t.TempDir()
	writeStateFile(t, dir, "tray-state.json", `{"device_uid":"dev","portal_url":"https://portal","auth_token":"device"}`)

	state, path := readSharedStateFrom(dir)
	if state.AuthToken != "device" || state.UIToken != "" || filepath.Base(path) != "tray-state.json" {
		t.Fatalf("read %s: %#v", path, state)
	}
}

func TestSharedStateIsEmptyWhenNothingIsReadable(t *testing.T) {
	state, path := readSharedStateFrom(t.TempDir())
	if state != (sharedState{}) || path != "" {
		t.Fatalf("read %q: %#v", path, state)
	}
}
