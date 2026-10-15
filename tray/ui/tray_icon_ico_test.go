package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"image"
	"image/color"
	"image/png"
	"testing"
)

type testICOImage struct {
	width, height int
	bitCount      int
	data          []byte
}

func buildTestICO(images ...testICOImage) []byte {
	var buf bytes.Buffer
	_ = binary.Write(&buf, binary.LittleEndian, []uint16{0, 1, uint16(len(images))})
	offset := icoHeaderSize + icoEntrySize*len(images)
	for _, img := range images {
		buf.WriteByte(byte(img.width % 256))
		buf.WriteByte(byte(img.height % 256))
		buf.Write([]byte{0, 0})
		_ = binary.Write(&buf, binary.LittleEndian, []uint16{1, uint16(img.bitCount)})
		_ = binary.Write(&buf, binary.LittleEndian, []uint32{uint32(len(img.data)), uint32(offset)})
		offset += len(img.data)
	}
	for _, img := range images {
		buf.Write(img.data)
	}
	return buf.Bytes()
}

func testPNG(t *testing.T, size int, c color.NRGBA) []byte {
	t.Helper()
	img := image.NewNRGBA(image.Rect(0, 0, size, size))
	for i := 0; i < len(img.Pix); i += 4 {
		img.Pix[i], img.Pix[i+1], img.Pix[i+2], img.Pix[i+3] = c.R, c.G, c.B, c.A
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encode PNG: %v", err)
	}
	return buf.Bytes()
}

// buildTestBitmap encodes an .ico BMP image. rows holds the packed colour
// rows top-down; mask holds one bool per pixel (true = transparent).
func buildTestBitmap(width, height, bitCount int, palette []color.NRGBA, rows [][]byte, mask [][]bool) []byte {
	var buf bytes.Buffer
	_ = binary.Write(&buf, binary.LittleEndian, uint32(bmpInfoHeaderSize))
	_ = binary.Write(&buf, binary.LittleEndian, int32(width))
	_ = binary.Write(&buf, binary.LittleEndian, int32(height*2))
	_ = binary.Write(&buf, binary.LittleEndian, []uint16{1, uint16(bitCount)})
	_ = binary.Write(&buf, binary.LittleEndian, []uint32{0, 0, 0, 0, uint32(len(palette)), 0})
	for _, c := range palette {
		buf.Write([]byte{c.B, c.G, c.R, 0})
	}
	colorStride := (width*bitCount + 31) / 32 * 4
	for y := height - 1; y >= 0; y-- {
		row := make([]byte, colorStride)
		copy(row, rows[y])
		buf.Write(row)
	}
	maskStride := (width + 31) / 32 * 4
	for y := height - 1; y >= 0; y-- {
		row := make([]byte, maskStride)
		for x := 0; x < width; x++ {
			if mask != nil && mask[y][x] {
				row[x/8] |= 0x80 >> (x % 8)
			}
		}
		buf.Write(row)
	}
	return buf.Bytes()
}

func decodeTestPNG(t *testing.T, data []byte) *image.NRGBA {
	t.Helper()
	img, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		t.Fatalf("result is not a PNG: %v", err)
	}
	out := image.NewNRGBA(img.Bounds())
	for y := img.Bounds().Min.Y; y < img.Bounds().Max.Y; y++ {
		for x := img.Bounds().Min.X; x < img.Bounds().Max.X; x++ {
			out.Set(x, y, img.At(x, y))
		}
	}
	return out
}

func assertPixel(t *testing.T, img *image.NRGBA, x, y int, want color.NRGBA) {
	t.Helper()
	if got := img.NRGBAAt(x, y); got != want {
		t.Errorf("pixel (%d,%d) = %v, want %v", x, y, got, want)
	}
}

func TestTrayIconPNGFromICOPicksSmallestImageAtLeastTarget(t *testing.T) {
	red := color.NRGBA{R: 0xFF, A: 0xFF}
	green := color.NRGBA{G: 0xFF, A: 0xFF}
	blue := color.NRGBA{B: 0xFF, A: 0xFF}
	ico := buildTestICO(
		testICOImage{width: 16, height: 16, bitCount: 32, data: testPNG(t, 16, red)},
		testICOImage{width: 256, height: 256, bitCount: 32, data: testPNG(t, 256, blue)},
		testICOImage{width: 32, height: 32, bitCount: 32, data: testPNG(t, 32, green)},
	)

	out, err := trayIconPNGFromICO(ico, 32)
	if err != nil {
		t.Fatalf("trayIconPNGFromICO: %v", err)
	}
	img := decodeTestPNG(t, out)
	if img.Bounds().Dx() != 32 {
		t.Fatalf("picked %dpx image, want 32px", img.Bounds().Dx())
	}
	assertPixel(t, img, 0, 0, green)
}

func TestTrayIconPNGFromICOFallsBackToLargestSmallerImage(t *testing.T) {
	ico := buildTestICO(
		testICOImage{width: 16, height: 16, bitCount: 32, data: testPNG(t, 16, color.NRGBA{R: 1, A: 0xFF})},
		testICOImage{width: 24, height: 24, bitCount: 32, data: testPNG(t, 24, color.NRGBA{R: 2, A: 0xFF})},
	)

	out, err := trayIconPNGFromICO(ico, 32)
	if err != nil {
		t.Fatalf("trayIconPNGFromICO: %v", err)
	}
	if got := decodeTestPNG(t, out).Bounds().Dx(); got != 24 {
		t.Fatalf("picked %dpx image, want 24px", got)
	}
}

func TestTrayIconPNGFromICOSkipsUndecodableImage(t *testing.T) {
	ico := buildTestICO(
		testICOImage{width: 32, height: 32, bitCount: 32, data: []byte("\x89PNG\r\n\x1a\nbroken")},
		testICOImage{width: 16, height: 16, bitCount: 32, data: testPNG(t, 16, color.NRGBA{B: 0xFF, A: 0xFF})},
	)

	out, err := trayIconPNGFromICO(ico, 32)
	if err != nil {
		t.Fatalf("trayIconPNGFromICO: %v", err)
	}
	if got := decodeTestPNG(t, out).Bounds().Dx(); got != 16 {
		t.Fatalf("picked %dpx image, want the decodable 16px one", got)
	}
}

func TestTrayIconPNGFromICODecodes32BitBitmapWithAlpha(t *testing.T) {
	// 2x2 image: top row opaque red then half-transparent green; bottom row
	// blue then fully transparent. Stored bottom-up as BGRA.
	rows := [][]byte{
		{0, 0, 0xFF, 0xFF, 0, 0xFF, 0, 0x80},
		{0xFF, 0, 0, 0xFF, 0, 0, 0, 0},
	}
	ico := buildTestICO(testICOImage{width: 2, height: 2, bitCount: 32, data: buildTestBitmap(2, 2, 32, nil, rows, nil)})

	out, err := trayIconPNGFromICO(ico, 32)
	if err != nil {
		t.Fatalf("trayIconPNGFromICO: %v", err)
	}
	img := decodeTestPNG(t, out)
	assertPixel(t, img, 0, 0, color.NRGBA{R: 0xFF, A: 0xFF})
	assertPixel(t, img, 1, 0, color.NRGBA{G: 0xFF, A: 0x80})
	assertPixel(t, img, 0, 1, color.NRGBA{B: 0xFF, A: 0xFF})
	if a := img.NRGBAAt(1, 1).A; a != 0 {
		t.Errorf("pixel (1,1) alpha = %d, want 0", a)
	}
}

func TestTrayIconPNGFromICOUsesMaskWhen32BitAlphaIsEmpty(t *testing.T) {
	rows := [][]byte{
		{0, 0, 0xFF, 0, 0, 0xFF, 0, 0},
		{0xFF, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0},
	}
	mask := [][]bool{{false, true}, {false, false}}
	ico := buildTestICO(testICOImage{width: 2, height: 2, bitCount: 32, data: buildTestBitmap(2, 2, 32, nil, rows, mask)})

	out, err := trayIconPNGFromICO(ico, 32)
	if err != nil {
		t.Fatalf("trayIconPNGFromICO: %v", err)
	}
	img := decodeTestPNG(t, out)
	assertPixel(t, img, 0, 0, color.NRGBA{R: 0xFF, A: 0xFF})
	if a := img.NRGBAAt(1, 0).A; a != 0 {
		t.Errorf("masked pixel alpha = %d, want 0", a)
	}
	assertPixel(t, img, 1, 1, color.NRGBA{R: 0xFF, G: 0xFF, B: 0xFF, A: 0xFF})
}

func TestTrayIconPNGFromICODecodesPalettedBitmaps(t *testing.T) {
	palette := []color.NRGBA{{A: 0xFF}, {R: 0xFF, A: 0xFF}, {G: 0xFF, A: 0xFF}, {B: 0xFF, A: 0xFF}}
	mask := [][]bool{{false, false, false}, {false, false, true}}
	cases := []struct {
		bitCount int
		rows     [][]byte
	}{
		// Pixels (top-down): [1 2 3] [3 0 1]
		{bitCount: 8, rows: [][]byte{{1, 2, 3}, {3, 0, 1}}},
		{bitCount: 4, rows: [][]byte{{0x12, 0x30}, {0x30, 0x10}}},
		{bitCount: 1, rows: [][]byte{{0b10100000}, {0b10000000}}},
	}
	for _, tc := range cases {
		ico := buildTestICO(testICOImage{
			width: 3, height: 2, bitCount: tc.bitCount,
			data: buildTestBitmap(3, 2, tc.bitCount, palette[:min(len(palette), 1<<tc.bitCount)], tc.rows, mask),
		})
		out, err := trayIconPNGFromICO(ico, 32)
		if err != nil {
			t.Fatalf("%dbpp: trayIconPNGFromICO: %v", tc.bitCount, err)
		}
		img := decodeTestPNG(t, out)
		if tc.bitCount == 1 {
			// 1bpp only reaches palette entries 0 and 1.
			assertPixel(t, img, 0, 0, palette[1])
			assertPixel(t, img, 1, 0, palette[0])
			assertPixel(t, img, 2, 0, palette[1])
			assertPixel(t, img, 0, 1, palette[1])
		} else {
			assertPixel(t, img, 0, 0, palette[1])
			assertPixel(t, img, 1, 0, palette[2])
			assertPixel(t, img, 2, 0, palette[3])
			assertPixel(t, img, 0, 1, palette[3])
			assertPixel(t, img, 1, 1, palette[0])
		}
		if a := img.NRGBAAt(2, 1).A; a != 0 {
			t.Errorf("%dbpp: masked pixel alpha = %d, want 0", tc.bitCount, a)
		}
	}
}

func TestTrayIconPNGFromICODecodes24BitBitmap(t *testing.T) {
	rows := [][]byte{{0x10, 0x20, 0x30}}
	ico := buildTestICO(testICOImage{width: 1, height: 1, bitCount: 24, data: buildTestBitmap(1, 1, 24, nil, rows, nil)})

	out, err := trayIconPNGFromICO(ico, 32)
	if err != nil {
		t.Fatalf("trayIconPNGFromICO: %v", err)
	}
	assertPixel(t, decodeTestPNG(t, out), 0, 0, color.NRGBA{R: 0x30, G: 0x20, B: 0x10, A: 0xFF})
}

func TestTrayIconPNGFromICOAcceptsPlainPNG(t *testing.T) {
	data := testPNG(t, 32, color.NRGBA{R: 0xFF, A: 0xFF})
	out, err := trayIconPNGFromICO(data, 32)
	if err != nil {
		t.Fatalf("trayIconPNGFromICO: %v", err)
	}
	if !bytes.Equal(out, data) {
		t.Fatal("plain PNG should be returned unchanged")
	}
}

func TestTrayIconPNGFromICORejectsBadInput(t *testing.T) {
	valid := buildTestICO(testICOImage{width: 16, height: 16, bitCount: 32, data: testPNG(t, 16, color.NRGBA{A: 0xFF})})
	truncatedBitmap := buildTestICO(testICOImage{
		width: 16, height: 16, bitCount: 32,
		data: buildTestBitmap(16, 16, 32, nil, make([][]byte, 16), nil)[:bmpInfoHeaderSize+10],
	})
	outOfRange := append([]byte(nil), valid...)
	binary.LittleEndian.PutUint32(outOfRange[18:22], uint32(len(valid)))

	cases := map[string][]byte{
		"empty":            nil,
		"not an icon":      []byte("<html>not found</html>"),
		"no images":        {0, 0, 1, 0, 0, 0},
		"truncated dir":    valid[:icoHeaderSize+8],
		"data out of file": outOfRange,
		"truncated bitmap": truncatedBitmap,
		"broken PNG":       []byte("\x89PNG\r\n\x1a\nbroken"),
	}
	for name, data := range cases {
		if _, err := trayIconPNGFromICO(data, 32); !errors.Is(err, errTrayIconUnusable) {
			t.Errorf("%s: err = %v, want errTrayIconUnusable", name, err)
		}
	}
}
