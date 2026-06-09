//go:build windows

package main

import (
	"github.com/bradhawkins85/myportal-tray/internal/logger"
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

// deviceUIDFromRegistry reads HKLM\Software\MyPortal\Tray\DeviceUID, which
// is written by the tray service after successful enrolment.
func deviceUIDFromRegistry() string {
	k, err := registry.OpenKey(registry.LOCAL_MACHINE, trayRegPath, registry.QUERY_VALUE)
	if err != nil {
		return ""
	}
	defer k.Close()
	v, _, err := k.GetStringValue("DeviceUID")
	if err != nil {
		return ""
	}
	return v
}

const ticketPrefillRegPath = `Software\MyPortal\Tray\TicketPrefill`

// loadTicketPrefill reads saved name, email, and phone from
// HKCU\Software\MyPortal\Tray\TicketPrefill so the dialog is pre-filled
// with values the user entered last time.
func loadTicketPrefill() (name, email, phone string) {
	k, err := registry.OpenKey(registry.CURRENT_USER, ticketPrefillRegPath, registry.QUERY_VALUE)
	if err != nil {
		return "", "", ""
	}
	defer k.Close()
	name, _, _ = k.GetStringValue("Name")
	email, _, _ = k.GetStringValue("Email")
	phone, _, _ = k.GetStringValue("Phone")
	return name, email, phone
}

// saveTicketPrefill persists name, email, and phone to
// HKCU\Software\MyPortal\Tray\TicketPrefill for the next time the dialog opens.
func saveTicketPrefill(name, email, phone string) {
	k, _, err := registry.CreateKey(
		registry.CURRENT_USER,
		ticketPrefillRegPath,
		registry.SET_VALUE,
	)
	if err != nil {
		logger.Warn("saveTicketPrefill: open HKCU prefill key: %v", err)
		return
	}
	defer k.Close()
	if err := k.SetStringValue("Name", name); err != nil {
		logger.Warn("saveTicketPrefill: save Name: %v", err)
	}
	if err := k.SetStringValue("Email", email); err != nil {
		logger.Warn("saveTicketPrefill: save Email: %v", err)
	}
	if err := k.SetStringValue("Phone", phone); err != nil {
		logger.Warn("saveTicketPrefill: save Phone: %v", err)
	}
}
