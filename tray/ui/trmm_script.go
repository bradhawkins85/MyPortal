package main

import (
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

func runTRMMScriptFromMenu(node api.MenuNode) {
	if node.ScriptID <= 0 {
		logger.Warn("TRMM script menu item %q has no script_id", node.Label)
		showTextWindow("Tactical RMM", "This menu item is missing a Tactical RMM script selection.")
		return
	}
	if strings.TrimSpace(gPortalURL) == "" || strings.TrimSpace(gAuthToken) == "" {
		logger.Warn("TRMM script request skipped: portal URL or auth token is missing")
		showTextWindow("Tactical RMM", "MyPortal is not connected yet. Please try again in a moment.")
		return
	}
	url := strings.TrimRight(gPortalURL, "/") + "/api/tray/trmm-script"
	body := []byte(fmt.Sprintf(`{"script_id":%d}`, node.ScriptID))
	req, err := newHTTPRequest(http.MethodPost, url, body)
	if err != nil {
		logger.Warn("TRMM script request build failed: %v", err)
		showTextWindow("Tactical RMM", "Could not build the Tactical RMM script request.")
		return
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		logger.Warn("TRMM script request failed: %v", err)
		showTextWindow("Tactical RMM", "Could not contact MyPortal to start the script.")
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		data, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		logger.Warn("TRMM script request HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(data)))
		showTextWindow("Tactical RMM", "MyPortal could not start the Tactical RMM script. Please contact support.")
		return
	}
	label := node.ScriptName
	if label == "" {
		label = node.Label
	}
	if label == "" {
		label = fmt.Sprintf("Script #%d", node.ScriptID)
	}
	showOSNotification("Script requested", trmmScriptSuccessMessage(label))
}

func trmmScriptSuccessMessage(label string) string {
	label = strings.TrimSpace(label)
	if label == "" {
		return "Your requested script has been executed and will run shortly."
	}
	return fmt.Sprintf("The script %q you requested has been executed and will run shortly.", label)
}
