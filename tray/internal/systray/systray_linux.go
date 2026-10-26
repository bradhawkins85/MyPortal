//go:build linux

package systray

import impl "fyne.io/systray"

// MenuItem is one entry in the tray menu.
type MenuItem = impl.MenuItem

func Run(onReady, onExit func()) { impl.Run(onReady, onExit) }
func Quit()                      { impl.Quit() }

// SetIcon takes an .ico on Windows and a PNG on Linux.
func SetIcon(icon []byte)       { impl.SetIcon(icon) }
func SetTitle(title string)     { impl.SetTitle(title) }
func SetTooltip(tooltip string) { impl.SetTooltip(tooltip) }
func AddSeparator()             { impl.AddSeparator() }
func AddMenuItem(title, tooltip string) *MenuItem {
	return impl.AddMenuItem(title, tooltip)
}
