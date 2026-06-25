//go:build windows

package main

import (
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// openNewTicketDialog opens the portal-hosted ticket form in the user's
// browser.  The earlier Windows implementation generated and executed a large
// temporary PowerShell/WinForms script with ExecutionPolicy Bypass. Although it
// only submitted helpdesk tickets, that malware-like execution pattern can trip
// Defender ML. Keeping the form in the signed portal web app avoids dynamic
// script execution from myportal-tray-ui.exe.
func openNewTicketDialog(_ *api.ConfigResponse) {
	if strings.TrimSpace(gPortalURL) == "" {
		logger.Warn("openNewTicketDialog: portal URL not available")
		return
	}
	openBrowser(strings.TrimRight(gPortalURL, "/") + "/tickets/new")
}

func openSyncroTicketDialog(_ *api.ConfigResponse) {
	openNewTicketDialog(nil)
}
