//go:build nowebview && windows

package main

import (
	"bytes"
	"image"
	"image/color"
	"image/png"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// The Windows notify icon loads .ico data.
var trayIconForPlatform = trayIconICOBytes

func defaultTrayIcon() []byte { return defaultIconICO() }

// defaultIconICO generates a minimal PNG-in-ICO icon at runtime so that no
// binary asset file needs to be committed.  A 16×16 solid MyPortal-blue
// (#0070C0) square is used as the placeholder until branding is loaded.
func defaultIconICO() []byte {
	img := image.NewRGBA(image.Rect(0, 0, 16, 16))
	blue := color.RGBA{R: 0x00, G: 0x70, B: 0xC0, A: 0xFF}
	for y := 0; y < 16; y++ {
		for x := 0; x < 16; x++ {
			img.Set(x, y, blue)
		}
	}

	var pngBuf bytes.Buffer
	if err := png.Encode(&pngBuf, img); err != nil {
		logger.Error("defaultIconICO: failed to encode PNG: %v", err)
		return nil
	}
	pngData := pngBuf.Bytes()
	pngLen := uint32(len(pngData))

	// ICO container: 6-byte ICONDIR + 16-byte ICONDIRENTRY + PNG payload.
	// Windows Vista+ supports PNG-encoded images inside ICO containers.
	buf := new(bytes.Buffer)

	// GRPICONDIR
	buf.Write([]byte{0, 0}) // reserved
	buf.Write([]byte{1, 0}) // type = 1 (icon)
	buf.Write([]byte{1, 0}) // count = 1

	// ICONDIRENTRY (16 bytes)
	buf.WriteByte(16)        // width
	buf.WriteByte(16)        // height
	buf.WriteByte(0)         // colorCount (0 = true color)
	buf.WriteByte(0)         // reserved
	buf.Write([]byte{1, 0})  // planes
	buf.Write([]byte{32, 0}) // bitCount
	// size of image data (4 bytes LE)
	buf.Write([]byte{byte(pngLen), byte(pngLen >> 8), byte(pngLen >> 16), byte(pngLen >> 24)})
	// offset to image data = 6 (header) + 16 (entry) = 22 (4 bytes LE)
	buf.Write([]byte{22, 0, 0, 0})

	buf.Write(pngData)
	return buf.Bytes()
}
