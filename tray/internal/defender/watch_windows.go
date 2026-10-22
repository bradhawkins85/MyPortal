//go:build windows

package defender

import (
	"context"
	"fmt"
	"unsafe"

	"golang.org/x/sys/windows"
)

var eventAPI = windows.NewLazySystemDLL("wevtapi.dll")
var evtSubscribe = eventAPI.NewProc("EvtSubscribe")
var evtNext = eventAPI.NewProc("EvtNext")
var evtClose = eventAPI.NewProc("EvtClose")

// WatchDetections subscribes locally; idle endpoints make no HTTP requests or
// repeated PowerShell queries. Detection and remediation events wake the uploader.
func WatchDetections(ctx context.Context, changed func()) error {
	signal, err := windows.CreateEvent(nil, 1, 0, nil)
	if err != nil {
		return err
	}
	defer windows.CloseHandle(signal)
	channel, _ := windows.UTF16PtrFromString("Microsoft-Windows-Windows Defender/Operational")
	query, _ := windows.UTF16PtrFromString("*[System[(EventID=1116 or EventID=1117 or EventID=1118 or EventID=1119)]]")
	subscription, _, callErr := evtSubscribe.Call(0, uintptr(signal), uintptr(unsafe.Pointer(channel)), uintptr(unsafe.Pointer(query)), 0, 0, 0, 1)
	if subscription == 0 {
		return fmt.Errorf("subscribe to Defender events: %w", callErr)
	}
	defer evtClose.Call(subscription)
	for ctx.Err() == nil {
		result, err := windows.WaitForSingleObject(signal, 1000)
		if err != nil {
			return err
		}
		if result == uint32(windows.WAIT_TIMEOUT) {
			continue
		}
		if result != windows.WAIT_OBJECT_0 {
			return fmt.Errorf("Defender event wait: %d", result)
		}
		if err := windows.ResetEvent(signal); err != nil {
			return err
		}
		sawEvents := false
		for {
			var events [32]uintptr
			var count uint32
			ok, _, nextErr := evtNext.Call(subscription, uintptr(len(events)), uintptr(unsafe.Pointer(&events[0])), 0, 0, uintptr(unsafe.Pointer(&count)))
			if ok == 0 {
				if nextErr == windows.ERROR_NO_MORE_ITEMS || nextErr == windows.ERROR_TIMEOUT {
					break
				}
				return fmt.Errorf("read Defender events: %w", nextErr)
			}
			for _, event := range events[:count] {
				evtClose.Call(event)
			}
			sawEvents = true
		}
		if sawEvents {
			changed()
		}
	}
	return ctx.Err()
}
