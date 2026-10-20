// tray_icon_cache.go keeps the last portal tray icon on disk so the menu bar
// shows the branded icon as soon as the UI starts, instead of waiting for the
// network after a reboot or upgrade. It has no build tag so it can be unit
// tested on every platform.
package main

import (
	"bytes"
	"errors"
	"fmt"
	"image/png"
	"os"
	"path/filepath"
)

const (
	trayIconCacheDirName  = "MyPortal Tray"
	trayIconCacheFileName = "tray-icon.png"
	maxCachedTrayIconSize = 1024 * 1024
)

// trayIconCachePath returns the per-user cache file for the tray icon, or ""
// when the user has no cache directory.
func trayIconCachePath() string {
	base, err := os.UserCacheDir()
	if err != nil || base == "" {
		return ""
	}
	return filepath.Join(base, trayIconCacheDirName, trayIconCacheFileName)
}

// loadCachedTrayIcon returns the cached PNG icon at path if it is a valid PNG.
func loadCachedTrayIcon(path string) ([]byte, error) {
	if path == "" {
		return nil, errors.New("no tray icon cache path")
	}
	info, err := os.Stat(path)
	if err != nil {
		return nil, err
	}
	if info.Size() > maxCachedTrayIconSize {
		return nil, fmt.Errorf("cached tray icon is larger than %d bytes", maxCachedTrayIconSize)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if !bytes.HasPrefix(data, pngSignature) {
		return nil, errors.New("cached tray icon is not a PNG")
	}
	if _, err := png.DecodeConfig(bytes.NewReader(data)); err != nil {
		return nil, fmt.Errorf("cached tray icon is not a valid PNG: %v", err)
	}
	return data, nil
}

// saveCachedTrayIcon writes the PNG icon to path atomically, so a UI that
// starts while the file is being replaced never reads a partial icon.
func saveCachedTrayIcon(path string, data []byte) error {
	if path == "" {
		return errors.New("no tray icon cache path")
	}
	if existing, err := os.ReadFile(path); err == nil && bytes.Equal(existing, data) {
		return nil
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, trayIconCacheFileName+".*")
	if err != nil {
		return err
	}
	tmpPath := tmp.Name()
	_, writeErr := tmp.Write(data)
	closeErr := tmp.Close()
	if err := errors.Join(writeErr, closeErr); err != nil {
		os.Remove(tmpPath)
		return err
	}
	if err := os.Rename(tmpPath, path); err != nil {
		os.Remove(tmpPath)
		return err
	}
	return nil
}
