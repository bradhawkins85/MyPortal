package main

import (
	"errors"
	"image/color"
	"reflect"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func TestTrayIconURLsPrefersConfigBrandingIcon(t *testing.T) {
	cases := []struct {
		name     string
		portal   string
		branding string
		want     []string
	}{
		{"no branding", "https://portal.example/", "", []string{"https://portal.example/tray/icon.ico"}},
		{"absolute branding", "https://portal.example", " https://cdn.example/logo.ico ",
			[]string{"https://cdn.example/logo.ico", "https://portal.example/tray/icon.ico"}},
		{"portal path branding", "https://portal.example/", "/uploads/brand.png",
			[]string{"https://portal.example/uploads/brand.png", "https://portal.example/tray/icon.ico"}},
		{"branding is the global icon", "https://portal.example", "https://portal.example/tray/icon.ico",
			[]string{"https://portal.example/tray/icon.ico"}},
		{"unsupported scheme ignored", "https://portal.example", "file:///etc/passwd",
			[]string{"https://portal.example/tray/icon.ico"}},
		{"protocol-relative ignored", "https://portal.example", "//cdn.example/logo.ico",
			[]string{"https://portal.example/tray/icon.ico"}},
		{"absolute branding without portal", "", "https://cdn.example/logo.ico",
			[]string{"https://cdn.example/logo.ico"}},
		{"nothing known", "", "/uploads/brand.png", nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := trayIconURLs(tc.portal, &api.ConfigResponse{BrandingIconURL: tc.branding})
			if !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("trayIconURLs = %q, want %q", got, tc.want)
			}
		})
	}
	if got := trayIconURLs("https://portal.example", nil); len(got) != 1 {
		t.Fatalf("nil config: got %q", got)
	}
}

func TestFetchFirstTrayIconFallsBackToGlobalIcon(t *testing.T) {
	icon := testPNG(t, 32, color.NRGBA{R: 0xFF, A: 0xFF})
	fetch := func(u string) ([]byte, error) {
		if u == "https://cdn.example/logo.ico" {
			return []byte("<html>not found</html>"), nil
		}
		return buildTestICO(testICOImage{width: 32, height: 32, bitCount: 32, data: icon}), nil
	}
	convert := func(data []byte) ([]byte, error) { return trayIconPNGFromICO(data, 32) }
	data, from, err := fetchFirstTrayIcon(
		[]string{"https://cdn.example/logo.ico", "https://portal.example/tray/icon.ico"}, fetch, convert)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if from != "https://portal.example/tray/icon.ico" || len(data) == 0 {
		t.Fatalf("got %d bytes from %q", len(data), from)
	}
}

func TestFetchFirstTrayIconErrorKinds(t *testing.T) {
	convert := func(data []byte) ([]byte, error) { return trayIconPNGFromICO(data, 32) }
	urls := []string{"https://a.example/i.ico", "https://b.example/i.ico"}

	_, _, err := fetchFirstTrayIcon(urls, func(string) ([]byte, error) { return []byte("junk"), nil }, convert)
	if !errors.Is(err, errTrayIconUnusable) {
		t.Fatalf("all unusable: want errTrayIconUnusable, got %v", err)
	}

	_, _, err = fetchFirstTrayIcon(urls, func(u string) ([]byte, error) {
		if u == urls[0] {
			return []byte("junk"), nil
		}
		return nil, errors.New("connection refused")
	}, convert)
	if err == nil || errors.Is(err, errTrayIconUnusable) {
		t.Fatalf("transient failure must be retryable, got %v", err)
	}

	if _, _, err = fetchFirstTrayIcon(nil, nil, convert); err == nil || errors.Is(err, errTrayIconUnusable) {
		t.Fatalf("no URLs must be retryable, got %v", err)
	}
}

func TestTrayIconICOBytes(t *testing.T) {
	ico := buildTestICO(testICOImage{width: 16, height: 16, bitCount: 32, data: testPNG(t, 16, color.NRGBA{A: 0xFF})})
	if got, err := trayIconICOBytes(ico); err != nil || !reflect.DeepEqual(got, ico) {
		t.Fatalf(".ico should pass through unchanged, err=%v", err)
	}

	pngData := testPNG(t, 48, color.NRGBA{G: 0xFF, A: 0xFF})
	wrapped, err := trayIconICOBytes(pngData)
	if err != nil {
		t.Fatalf("wrap PNG: %v", err)
	}
	entries, err := parseICOEntries(wrapped)
	if err != nil || len(entries) != 1 || entries[0].width != 48 || !reflect.DeepEqual(entries[0].data, pngData) {
		t.Fatalf("wrapped PNG not a valid single-image .ico: %v %+v", err, entries)
	}

	if _, err := trayIconICOBytes([]byte("GIF89a")); !errors.Is(err, errTrayIconUnusable) {
		t.Fatalf("unsupported format: want errTrayIconUnusable, got %v", err)
	}
	if _, err := trayIconICOBytes(testPNG(t, 300, color.NRGBA{A: 0xFF})); !errors.Is(err, errTrayIconUnusable) {
		t.Fatalf("oversized PNG: want errTrayIconUnusable, got %v", err)
	}
}
