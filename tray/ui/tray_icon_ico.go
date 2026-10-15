// tray_icon_ico.go converts the portal's tray icon (/tray/icon.ico) into a PNG.
// The macOS menu bar cannot reliably render the multi-image Windows .ico
// payload that admins upload, so the darwin build hands systray a single PNG
// picked from the .ico instead. It has no build tag so the decoder can be unit
// tested on every platform.
package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"sort"
)

// errTrayIconUnusable marks icon payloads that can never be displayed, so
// callers can stop retrying instead of re-downloading the same bad file.
var errTrayIconUnusable = errors.New("unusable tray icon")

var pngSignature = []byte("\x89PNG\r\n\x1a\n")

const (
	icoHeaderSize     = 6
	icoEntrySize      = 16
	bmpInfoHeaderSize = 40
	maxIcoDimension   = 256
)

type icoEntry struct {
	width, height int
	bitCount      int
	data          []byte
}

// trayIconPNGFromICO returns a PNG encoding of the image in an .ico payload
// that best suits a tray icon drawn targetPx pixels wide: the smallest image at
// least that big, otherwise the largest available. A payload that is already a
// PNG is accepted as-is. Errors wrap errTrayIconUnusable.
func trayIconPNGFromICO(data []byte, targetPx int) ([]byte, error) {
	if bytes.HasPrefix(data, pngSignature) {
		if _, err := png.DecodeConfig(bytes.NewReader(data)); err != nil {
			return nil, fmt.Errorf("%w: invalid PNG: %v", errTrayIconUnusable, err)
		}
		return data, nil
	}
	entries, err := parseICOEntries(data)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", errTrayIconUnusable, err)
	}
	sortICOEntries(entries, targetPx)

	var lastErr error
	for _, entry := range entries {
		img, decodeErr := decodeICOEntry(entry)
		if decodeErr != nil {
			lastErr = decodeErr
			continue
		}
		var out bytes.Buffer
		if encodeErr := png.Encode(&out, img); encodeErr != nil {
			lastErr = encodeErr
			continue
		}
		return out.Bytes(), nil
	}
	return nil, fmt.Errorf("%w: no decodable image in .ico: %v", errTrayIconUnusable, lastErr)
}

func parseICOEntries(data []byte) ([]icoEntry, error) {
	if len(data) < icoHeaderSize {
		return nil, errors.New("file too short for an .ico header")
	}
	if binary.LittleEndian.Uint16(data[0:2]) != 0 || binary.LittleEndian.Uint16(data[2:4]) != 1 {
		return nil, errors.New("missing .ico signature")
	}
	count := int(binary.LittleEndian.Uint16(data[4:6]))
	if count == 0 {
		return nil, errors.New(".ico contains no images")
	}
	if len(data) < icoHeaderSize+count*icoEntrySize {
		return nil, errors.New(".ico directory is truncated")
	}

	entries := make([]icoEntry, 0, count)
	for i := 0; i < count; i++ {
		e := data[icoHeaderSize+i*icoEntrySize:]
		size := uint64(binary.LittleEndian.Uint32(e[8:12]))
		offset := uint64(binary.LittleEndian.Uint32(e[12:16]))
		if size == 0 || offset+size > uint64(len(data)) {
			continue
		}
		width, height := int(e[0]), int(e[1])
		if width == 0 {
			width = maxIcoDimension
		}
		if height == 0 {
			height = maxIcoDimension
		}
		entries = append(entries, icoEntry{
			width:    width,
			height:   height,
			bitCount: int(binary.LittleEndian.Uint16(e[6:8])),
			data:     data[offset : offset+size],
		})
	}
	if len(entries) == 0 {
		return nil, errors.New(".ico image data lies outside the file")
	}
	return entries, nil
}

// sortICOEntries orders entries from most to least preferred: images at least
// targetPx wide (smallest first), then smaller images (largest first), with
// deeper colour winning ties.
func sortICOEntries(entries []icoEntry, targetPx int) {
	sort.SliceStable(entries, func(i, j int) bool {
		a, b := entries[i], entries[j]
		aFits, bFits := a.width >= targetPx, b.width >= targetPx
		if aFits != bFits {
			return aFits
		}
		if a.width != b.width {
			if aFits {
				return a.width < b.width
			}
			return a.width > b.width
		}
		return a.bitCount > b.bitCount
	})
}

func decodeICOEntry(entry icoEntry) (image.Image, error) {
	if bytes.HasPrefix(entry.data, pngSignature) {
		cfg, err := png.DecodeConfig(bytes.NewReader(entry.data))
		if err != nil {
			return nil, err
		}
		// Real .ico images are at most 256px; refuse anything far larger
		// rather than allocating whatever the PNG header claims.
		if cfg.Width > 4*maxIcoDimension || cfg.Height > 4*maxIcoDimension {
			return nil, fmt.Errorf("embedded PNG is too large (%dx%d)", cfg.Width, cfg.Height)
		}
		return png.Decode(bytes.NewReader(entry.data))
	}
	return decodeICOBitmap(entry.data)
}

// decodeICOBitmap decodes the BMP form of an .ico image: a BITMAPINFOHEADER,
// an optional palette, bottom-up colour rows, then a 1bpp transparency mask.
func decodeICOBitmap(data []byte) (image.Image, error) {
	if len(data) < bmpInfoHeaderSize {
		return nil, errors.New("bitmap header is truncated")
	}
	headerSize := binary.LittleEndian.Uint32(data[0:4])
	if headerSize < bmpInfoHeaderSize || uint64(headerSize) > uint64(len(data)) {
		return nil, errors.New("unsupported bitmap header")
	}
	width := int(int32(binary.LittleEndian.Uint32(data[4:8])))
	// The stored height covers both the colour rows and the mask rows.
	height := int(int32(binary.LittleEndian.Uint32(data[8:12]))) / 2
	bitCount := int(binary.LittleEndian.Uint16(data[14:16]))
	compression := binary.LittleEndian.Uint32(data[16:20])
	colorsUsed := binary.LittleEndian.Uint32(data[32:36])
	if width <= 0 || width > maxIcoDimension || height <= 0 || height > maxIcoDimension {
		return nil, fmt.Errorf("unsupported bitmap size %dx%d", width, height)
	}
	if compression != 0 {
		return nil, fmt.Errorf("unsupported bitmap compression %d", compression)
	}

	offset := int(headerSize)
	var palette []color.NRGBA
	switch bitCount {
	case 1, 4, 8:
		paletteLen := 1 << bitCount
		if colorsUsed > 0 && colorsUsed < uint32(paletteLen) {
			paletteLen = int(colorsUsed)
		}
		if len(data) < offset+paletteLen*4 {
			return nil, errors.New("bitmap palette is truncated")
		}
		palette = make([]color.NRGBA, paletteLen)
		for i := range palette {
			p := data[offset+i*4:]
			palette[i] = color.NRGBA{R: p[2], G: p[1], B: p[0], A: 0xFF}
		}
		offset += paletteLen * 4
	case 24, 32:
	default:
		return nil, fmt.Errorf("unsupported bitmap depth %d bpp", bitCount)
	}

	colorStride := (width*bitCount + 31) / 32 * 4
	maskStride := (width + 31) / 32 * 4
	colorRows := data[offset:]
	if len(colorRows) < colorStride*height {
		return nil, errors.New("bitmap pixel data is truncated")
	}
	// Some 32bpp icons omit the mask because the alpha channel replaces it.
	var maskRows []byte
	if rest := colorRows[colorStride*height:]; len(rest) >= maskStride*height {
		maskRows = rest
	}

	img := image.NewNRGBA(image.Rect(0, 0, width, height))
	hasAlpha := false
	for y := 0; y < height; y++ {
		row := colorRows[(height-1-y)*colorStride:]
		for x := 0; x < width; x++ {
			var c color.NRGBA
			switch bitCount {
			case 32:
				c = color.NRGBA{R: row[x*4+2], G: row[x*4+1], B: row[x*4], A: row[x*4+3]}
				hasAlpha = hasAlpha || c.A != 0
			case 24:
				c = color.NRGBA{R: row[x*3+2], G: row[x*3+1], B: row[x*3], A: 0xFF}
			default:
				bitPos := x * bitCount
				index := int(row[bitPos/8]>>(8-bitCount-bitPos%8)) & (1<<bitCount - 1)
				if index >= len(palette) {
					return nil, errors.New("bitmap palette index out of range")
				}
				c = palette[index]
			}
			img.SetNRGBA(x, y, c)
		}
	}

	// Older 32bpp icons leave alpha at zero and rely on the mask instead.
	if bitCount == 32 && !hasAlpha {
		for i := 3; i < len(img.Pix); i += 4 {
			img.Pix[i] = 0xFF
		}
	}
	if maskRows != nil && (bitCount != 32 || !hasAlpha) {
		for y := 0; y < height; y++ {
			row := maskRows[(height-1-y)*maskStride:]
			for x := 0; x < width; x++ {
				if row[x/8]&(0x80>>(x%8)) != 0 {
					img.Pix[img.PixOffset(x, y)+3] = 0
				}
			}
		}
	}
	return img, nil
}
