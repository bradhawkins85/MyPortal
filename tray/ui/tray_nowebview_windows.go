//go:build nowebview && windows

// tray_nowebview_windows.go wires up a real Windows system-tray icon for the
// CGO=0 (nowebview) build.  getlantern/systray uses pure-Go Win32 syscalls on
// Windows, so CGO is not required.
package main

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
	"os"

	"github.com/getlantern/systray"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// runUI starts the systray event loop; it blocks until systray.Quit() is called.
func runUI() {
	logger.Info("MyPortal Tray UI (Windows) starting systray")
	systray.Run(onTrayReady, onTrayExit)
}

func onTrayReady() {
	systray.SetTitle("MyPortal")
	systray.SetTooltip("MyPortal Helpdesk")
	systray.SetIcon(defaultIconICO())
	buildMenu(gConfig)
}

func onTrayExit() {
	if gIPCConn != nil {
		gIPCConn.Close()
	}
}

// buildMenu constructs the systray menu from a ConfigResponse.
// Note: getlantern/systray v1.2.2 does not expose a ResetMenu API, so this
// function is only called once (on startup). Dynamic menu rebuilding is
// deferred to a future systray library upgrade.
func buildMenu(cfg *api.ConfigResponse) {
	if cfg == nil {
		cfg = defaultConfig()
	}
	for _, node := range cfg.Menu {
		addNode(node, cfg)
	}
	systray.AddSeparator()
	quitItem := systray.AddMenuItem("Quit", "Quit MyPortal Tray")
	go func() {
		<-quitItem.ClickedCh
		systray.Quit()
	}()
}

func addNode(node api.MenuNode, cfg *api.ConfigResponse) {
	switch node.Type {
	case "separator":
		systray.AddSeparator()

	case "label":
		item := systray.AddMenuItem(node.Label, "")
		item.Disable()

	case "link":
		item := systray.AddMenuItem(node.Label, node.URL)
		go func(url string) {
			for range item.ClickedCh {
				openBrowser(url)
			}
		}(node.URL)

	case "open_chat":
		if !cfg.ChatEnabled {
			return
		}
		label := node.Label
		if label == "" {
			label = "Chat with helpdesk"
		}
		item := systray.AddMenuItem(label, "Open chat window")
		go func() {
			for range item.ClickedCh {
				openChatWindow("", cfg)
			}
		}()

	case "display_text":
		label := node.Label
		if label == "" {
			label = "Info"
		}
		item := systray.AddMenuItem(label, "")
		go func(text string) {
			for range item.ClickedCh {
				showTextWindow("Information", text)
			}
		}(cfg.DisplayText)

	case "env_var":
		label := node.Label
		if label == "" {
			label = node.Name
		}
		item := systray.AddMenuItem(label, "Click to copy value")
		go func(varName string) {
			for range item.ClickedCh {
				val := os.Getenv(varName)
				if val == "" {
					val = "(not set)"
				}
				showTextWindow(varName, val)
			}
		}(node.Name)

	case "submenu":
		if node.Label != "" {
			item := systray.AddMenuItem("▸ "+node.Label, "")
			item.Disable()
		}
		for _, child := range node.Children {
			addNode(*child, cfg)
		}
	}
}

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
