package main

import (
	"bytes"
	"image/color"
	"os"
	"path/filepath"
	"testing"
)

var cacheTestColor = color.NRGBA{R: 10, G: 20, B: 30, A: 255}

func TestTrayIconCacheRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "nested", trayIconCacheFileName)
	icon := testPNG(t, 4, cacheTestColor)
	if err := saveCachedTrayIcon(path, icon); err != nil {
		t.Fatalf("saveCachedTrayIcon: %v", err)
	}
	got, err := loadCachedTrayIcon(path)
	if err != nil {
		t.Fatalf("loadCachedTrayIcon: %v", err)
	}
	if !bytes.Equal(got, icon) {
		t.Fatal("cached icon does not match the saved icon")
	}
	entries, _ := os.ReadDir(filepath.Dir(path))
	if len(entries) != 1 {
		t.Fatalf("cache dir has %d entries, want only the icon", len(entries))
	}
}

func TestLoadCachedTrayIconRejectsBadFiles(t *testing.T) {
	dir := t.TempDir()
	if _, err := loadCachedTrayIcon(""); err == nil {
		t.Fatal("empty path must fail")
	}
	if _, err := loadCachedTrayIcon(filepath.Join(dir, "missing.png")); err == nil {
		t.Fatal("missing file must fail")
	}
	notPNG := filepath.Join(dir, "not.png")
	os.WriteFile(notPNG, []byte("GIF89a"), 0o600)
	if _, err := loadCachedTrayIcon(notPNG); err == nil {
		t.Fatal("non-PNG must fail")
	}
	truncated := filepath.Join(dir, "truncated.png")
	os.WriteFile(truncated, testPNG(t, 4, cacheTestColor)[:12], 0o600)
	if _, err := loadCachedTrayIcon(truncated); err == nil {
		t.Fatal("truncated PNG must fail")
	}
}
