//go:build darwin && !nowebview

package main

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/getlantern/systray"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// configureTrayIcon displays a local fallback immediately, then replaces it
// with the same portal-provided branded icon used by the Windows agent.
func configureTrayIcon() {
	systray.SetTitle("")
	systray.SetIcon(defaultDarwinIcon())
	go fetchAndSetDarwinIcon()
}

// fetchAndSetDarwinIcon uses the same portal branding endpoint as the Windows
// agent. NSImage accepts the ICO payload returned by this endpoint, allowing a
// custom tray icon to remain consistent across both desktop platforms.
func fetchAndSetDarwinIcon() {
	if strings.TrimSpace(gPortalURL) == "" {
		return
	}
	url := strings.TrimRight(gPortalURL, "/") + "/tray/icon.ico"
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		logger.Warn("Tray icon fetch failed: %v", err)
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		logger.Warn("Tray icon fetch HTTP %d, falling back to default", resp.StatusCode)
		return
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
	if err != nil {
		logger.Warn("Tray icon read error: %v", err)
		return
	}
	if len(data) < 4 || data[0] != 0 || data[1] != 0 || data[2] != 1 || data[3] != 0 {
		logger.Warn("Tray icon fetch returned invalid ICO data, falling back to default")
		return
	}
	systray.SetIcon(data)
	logger.Info("Tray icon updated from portal")
}

func defaultDarwinIcon() []byte {
	icon := image.NewNRGBA(image.Rect(0, 0, 22, 22))
	ink := color.NRGBA{R: 0, G: 0, B: 0, A: 255}
	for y := 3; y < 19; y++ {
		for x := 3; x < 19; x++ {
			dx, dy := x-11, y-11
			d2 := dx*dx + dy*dy
			if d2 >= 31 && d2 <= 64 {
				icon.SetNRGBA(x, y, ink)
			}
		}
	}
	var data bytes.Buffer
	if png.Encode(&data, icon) == nil {
		return data.Bytes()
	}
	return nil
}
