//go:build darwin && !nowebview

package main

/*
#cgo CFLAGS: -x objective-c
#cgo LDFLAGS: -framework Cocoa
#import <Cocoa/Cocoa.h>

static void setMenuBarActivationPolicy(void) {
	[NSApplication sharedApplication];
	[NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
}
*/
import "C"

// prepareMenuBarApp makes the UI a menu-bar (accessory) app before systray
// starts AppKit. The installed UI is an LSUIElement app bundle, which already
// gets this policy; the call covers the bare executable in development runs.
// macOS does not show the status item of a bare executable reliably even with
// this policy, so installs must use the bundle. It must run on the main
// thread, before systray.Run.
func prepareMenuBarApp() {
	C.setMenuBarActivationPolicy()
}
