// tray_icon_source.go works out where the tray icon is downloaded from. It has
// no build tag so the URL and format handling can be unit tested everywhere.
package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"image/png"
	"net/url"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// trayIconURLs returns the icon URLs to try, most specific first: the branding
// icon URL set on the device's tray configuration, then the portal-wide icon
// uploaded under Admin > Tray. The branding URL may be absolute (http/https)
// or a path on the portal; anything else is ignored.
func trayIconURLs(portalURL string, cfg *api.ConfigResponse) []string {
	base := strings.TrimRight(strings.TrimSpace(portalURL), "/")
	var urls []string
	if cfg != nil {
		if branding := resolveBrandingIconURL(base, cfg.BrandingIconURL); branding != "" {
			urls = append(urls, branding)
		}
	}
	if base != "" {
		if global := base + "/tray/icon.ico"; len(urls) == 0 || urls[0] != global {
			urls = append(urls, global)
		}
	}
	return urls
}

func resolveBrandingIconURL(base, raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	if strings.HasPrefix(raw, "/") && !strings.HasPrefix(raw, "//") {
		if base == "" {
			return ""
		}
		return base + raw
	}
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" || (u.Scheme != "https" && u.Scheme != "http") {
		return ""
	}
	return u.String()
}

// trayIconICOBytes returns icon data the Windows notify icon can load: an .ico
// as-is, or a PNG wrapped in a single-image .ico. Errors wrap
// errTrayIconUnusable.
func trayIconICOBytes(data []byte) ([]byte, error) {
	if len(data) >= icoHeaderSize && bytes.HasPrefix(data, []byte{0, 0, 1, 0}) {
		return data, nil
	}
	if !bytes.HasPrefix(data, pngSignature) {
		return nil, fmt.Errorf("%w: not an .ico or PNG file", errTrayIconUnusable)
	}
	cfg, err := png.DecodeConfig(bytes.NewReader(data))
	if err != nil {
		return nil, fmt.Errorf("%w: invalid PNG: %v", errTrayIconUnusable, err)
	}
	if cfg.Width > maxIcoDimension || cfg.Height > maxIcoDimension {
		return nil, fmt.Errorf("%w: PNG is larger than %dpx", errTrayIconUnusable, maxIcoDimension)
	}
	var buf bytes.Buffer
	_ = binary.Write(&buf, binary.LittleEndian, []uint16{0, 1, 1})
	// A width or height of 0 in the directory entry means 256.
	buf.Write([]byte{byte(cfg.Width % 256), byte(cfg.Height % 256), 0, 0})
	_ = binary.Write(&buf, binary.LittleEndian, []uint16{1, 32})
	_ = binary.Write(&buf, binary.LittleEndian, []uint32{uint32(len(data)), icoHeaderSize + icoEntrySize})
	buf.Write(data)
	return buf.Bytes(), nil
}

// fetchFirstTrayIcon downloads each URL in turn and returns the first payload
// that convert accepts. The error wraps errTrayIconUnusable only when every
// URL answered with an unusable icon, so callers retry transient failures.
func fetchFirstTrayIcon(urls []string, fetch func(string) ([]byte, error), convert func([]byte) ([]byte, error)) ([]byte, string, error) {
	if len(urls) == 0 {
		return nil, "", errors.New("portal URL not configured yet")
	}
	var errs []error
	allUnusable := true
	for _, u := range urls {
		data, err := fetch(u)
		if err == nil {
			data, err = convert(data)
		}
		if err == nil {
			return data, u, nil
		}
		if !errors.Is(err, errTrayIconUnusable) {
			allUnusable = false
		}
		errs = append(errs, fmt.Errorf("%s: %w", u, err))
	}
	err := errors.Join(errs...)
	if allUnusable {
		return nil, "", err
	}
	// Drop the unusable marker so the caller retries the transient failure.
	return nil, "", errors.New(err.Error())
}
