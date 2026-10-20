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
// starts AppKit. The UI is a bare executable without an Info.plist, so AppKit
// otherwise treats it as a background-only process, and macOS can hold back
// or never show its status item when launchd starts it at login or after an
// upgrade. It must run on the main thread, before systray.Run.
func prepareMenuBarApp() {
	C.setMenuBarActivationPolicy()
}
