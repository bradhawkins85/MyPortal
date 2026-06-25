//go:build windows

package main

import (
	"encoding/json"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

type ticketTokenResponse struct {
	Token     string `json:"token"`
	ExpiresIn int    `json:"expires_in"`
	TicketURL string `json:"ticket_url"`
}

// openNewTicketDialog opens the portal-hosted ticket form with a short-lived
// tray-authenticated URL. The server-side web form preserves the old modal's
// dynamic questions, required/conditional custom fields, and linked-computer
// ticket association without requiring the user to log in.
func openNewTicketDialog(_ *api.ConfigResponse) {
	openTicketForm("myportal", "Submit Ticket")
}

func openSyncroTicketDialog(_ *api.ConfigResponse) {
	openTicketForm("syncro", "Create Syncro Ticket")
}

func openTicketForm(mode, title string) {
	if strings.TrimSpace(gPortalURL) == "" || strings.TrimSpace(gAuthToken) == "" {
		showOSNotification(title, "Portal connection is not ready yet. Please try again shortly.")
		return
	}
	url := requestTicketFormURL(mode)
	if strings.TrimSpace(url) == "" {
		showOSNotification(title, "Could not open the ticket form. Please try again shortly.")
		return
	}
	openBrowser(url)
}

func requestTicketFormURL(mode string) string {
	body := []byte(`{"mode":"myportal"}`)
	if strings.EqualFold(mode, "syncro") {
		body = []byte(`{"mode":"syncro"}`)
	}
	req, err := newHTTPRequest("POST", strings.TrimRight(gPortalURL, "/")+"/api/tray/ticket-token", body)
	if err != nil {
		logger.Warn("requestTicketFormURL: build request: %v", err)
		return ""
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		logger.Warn("requestTicketFormURL: HTTP error: %v", err)
		return ""
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		logger.Warn("requestTicketFormURL: server returned HTTP %d", resp.StatusCode)
		return ""
	}
	var result ticketTokenResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		logger.Warn("requestTicketFormURL: decode response: %v", err)
		return ""
	}
	return strings.TrimSpace(result.TicketURL)
}
