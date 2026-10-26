// Package systray is the tray icon library used by the CGO-free (nowebview)
// tray UI: getlantern/systray on Windows (Win32 calls) and fyne.io/systray on
// Linux (the StatusNotifierItem D-Bus protocol, shown by KDE, Cinnamon, XFCE,
// MATE and GNOME with the AppIndicator extension). Both share one API, so the
// menu code in ui/tray_nowebview_systray.go is the same on both platforms.
package systray
