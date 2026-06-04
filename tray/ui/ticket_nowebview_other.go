//go:build nowebview && !windows

package main

import (
	"fmt"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// openNewTicketDialog opens the portal's ticket submission page in the
// default browser on non-Windows platforms (macOS / Linux).
func openNewTicketDialog(_ *api.ConfigResponse) {
	if gPortalURL == "" {
		fmt.Println("[submit_ticket] portal URL not available")
		return
	}
	openBrowser(gPortalURL + "/tickets/new")
}
