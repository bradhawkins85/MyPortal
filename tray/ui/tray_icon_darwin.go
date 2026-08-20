//go:build darwin && !nowebview

package main

import (
	"bytes"
	"image"
	"image/color"
	"image/png"

	"github.com/getlantern/systray"
)

// configureTrayIcon uses a monochrome template image so macOS can render the
// menu-bar item correctly in both light and dark modes. Without an image,
// systray falls back to displaying the application title as plain text.
func configureTrayIcon() {
	systray.SetTitle("")
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
		systray.SetTemplateIcon(data.Bytes(), data.Bytes())
	}
}
