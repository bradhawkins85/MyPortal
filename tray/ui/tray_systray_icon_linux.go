//go:build nowebview && linux

package main

import (
	"bytes"
	"image"
	"image/color"
	"image/png"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// StatusNotifierItem hosts take pixels, which systray decodes from a PNG.
func trayIconForPlatform(data []byte) ([]byte, error) {
	return trayIconPNGFromICO(data, 64)
}

// defaultTrayIcon is a solid MyPortal-blue (#0070C0) square shown until the
// branded icon has been downloaded.
func defaultTrayIcon() []byte {
	img := image.NewRGBA(image.Rect(0, 0, 32, 32))
	blue := color.RGBA{R: 0x00, G: 0x70, B: 0xC0, A: 0xFF}
	for y := 0; y < 32; y++ {
		for x := 0; x < 32; x++ {
			img.Set(x, y, blue)
		}
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		logger.Error("defaultTrayIcon: failed to encode PNG: %v", err)
		return nil
	}
	return buf.Bytes()
}
