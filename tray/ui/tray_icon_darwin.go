//go:build darwin && !nowebview

package main

import (
	"bytes"
	"errors"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"io"
	"net/http"
	"time"

	"github.com/getlantern/systray"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// darwinTrayIconPixels is the bitmap size picked from the portal .ico. systray
// draws the menu-bar image at 16x16 points, so 32px stays sharp on Retina.
const darwinTrayIconPixels = 32

// trayIconRefreshCh wakes the icon worker. It is buffered so a refresh
// requested while a fetch is in flight is kept rather than dropped.
var trayIconRefreshCh = make(chan struct{}, 1)

// trayIconMaxRetryDelay caps the wait between failed icon downloads. After a
// reboot the network often comes up a few seconds after login, so a long
// backoff would leave the menu bar on the fallback icon for minutes.
const trayIconMaxRetryDelay = time.Minute

// configureTrayIcon displays the last portal icon (or a local fallback)
// immediately, then replaces it with the same portal-provided branded icon
// used by the Windows agent.
func configureTrayIcon() {
	systray.SetTitle("")
	if cached, err := loadCachedTrayIcon(trayIconCachePath()); err == nil {
		systray.SetIcon(cached)
		logger.Info("Tray icon shown from cache while the portal icon is refreshed")
	} else {
		// A template image is drawn in the menu bar's text colour, so the
		// fallback stays visible on dark menu bars. A plain black image
		// disappears there until the portal icon has downloaded.
		fallback := defaultDarwinIcon()
		systray.SetTemplateIcon(fallback, fallback)
	}
	go runDarwinTrayIconWorker()
	requestTrayIconRefresh()
}

// requestTrayIconRefresh asks the icon worker to (re)load the portal icon. It
// is called on config_changed, which arrives once the service has enrolled
// the device, so a tray started before enrolment still picks up the icon.
func requestTrayIconRefresh() {
	select {
	case trayIconRefreshCh <- struct{}{}:
	default:
	}
}

// runDarwinTrayIconWorker loads the portal icon on each refresh request. The
// UI starts at login, often before the network is up, so failed downloads are
// retried with backoff until one succeeds.
func runDarwinTrayIconWorker() {
	for range trayIconRefreshCh {
		delay := 5 * time.Second
		for attempt := 1; ; attempt++ {
			err := fetchAndSetDarwinIcon()
			if err == nil {
				break
			}
			if errors.Is(err, errTrayIconUnusable) {
				logger.Warn("Tray icon from portal cannot be displayed, keeping default: %v", err)
				break
			}
			if attempt == 1 {
				logger.Warn("Tray icon fetch failed, will retry: %v", err)
			} else {
				logger.Debug("Tray icon fetch attempt %d failed: %v", attempt, err)
			}
			select {
			case <-time.After(delay):
			case <-trayIconRefreshCh:
			}
			if delay *= 2; delay > trayIconMaxRetryDelay {
				delay = trayIconMaxRetryDelay
			}
		}
	}
}

// fetchAndSetDarwinIcon downloads the device's branding icon (or the
// portal-wide .ico served to the Windows agent) and shows it as a PNG, which
// the menu bar renders reliably.
func fetchAndSetDarwinIcon() error {
	if gPortalURL == "" {
		// The service may have written the portal URL to its state file
		// since the last attempt.
		refreshPortalURL()
	}
	urls := trayIconURLs(gPortalURL, gConfig)
	icon, url, err := fetchFirstTrayIcon(urls, downloadTrayIcon, func(data []byte) ([]byte, error) {
		return trayIconPNGFromICO(data, darwinTrayIconPixels)
	})
	if err != nil {
		return err
	}
	systray.SetIcon(icon)
	logger.Info("Tray icon updated from %s", url)
	if err := saveCachedTrayIcon(trayIconCachePath(), icon); err != nil {
		logger.Debug("Tray icon cache not written: %v", err)
	}
	return nil
}

func downloadTrayIcon(url string) ([]byte, error) {
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
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
