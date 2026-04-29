//go:build nowebview && windows

package main

import (
	"golang.org/x/sys/windows/registry"
)

const trayRegPath = `Software\MyPortal\Tray`

// portalURLFromRegistry reads HKLM\Software\MyPortal\Tray\PortalURL, which
// is written by the MSI installer at install time. The UI agent uses this
// as a fallback when neither MYPORTAL_URL nor tray-state.json (written by
// the service after enrolment) provides a portal URL — this lets the tray
// icon be fetched from the server on the very first launch, before the
// service has finished enrolling the device.
func portalURLFromRegistry() string {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, trayRegPath, registry.QUERY_VALUE)
	if err != nil {
		return ""
	}
	defer k.Close()
	v, _, err := k.GetStringValue("PortalURL")
	if err != nil {
		return ""
	}
	return v
}
