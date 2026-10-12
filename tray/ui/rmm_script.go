package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

const rmmScriptWindowTitle = "Run script"

// runRMMScriptFromMenu asks MyPortal to run the MyPortal RMM script behind an
// rmm_script menu item on this computer. The script's values live on the
// server; the request only names the menu item.
func runRMMScriptFromMenu(node api.MenuNode) {
	label := rmmScriptLabel(node)
	logger.Info("RMM script menu item selected: label=%q script_id=%d node_id=%q", label, node.ScriptID, node.NodeID)
	if node.ScriptID <= 0 {
		logger.Warn("RMM script menu item %q has no script_id", label)
		showTextWindow(rmmScriptWindowTitle, "This menu item is missing a script selection.")
		return
	}
	if node.Confirm && !confirmAction(rmmScriptWindowTitle, fmt.Sprintf("Run %s on this computer now?", label)) {
		logger.Info("RMM script menu item %q cancelled by the user", label)
		return
	}
	if strings.TrimSpace(gPortalURL) == "" || strings.TrimSpace(gAuthToken) == "" {
		logger.Warn("RMM script request skipped: portal URL or auth token is missing")
		showTextWindow(rmmScriptWindowTitle, "MyPortal is not connected yet. Please try again in a moment.")
		return
	}
	body, err := json.Marshal(map[string]any{"script_id": node.ScriptID, "node_id": node.NodeID})
	if err != nil {
		showTextWindow(rmmScriptWindowTitle, "Could not build the script request.")
		return
	}
	url := strings.TrimRight(gPortalURL, "/") + "/api/tray/rmm-script"
	req, err := newHTTPRequest(http.MethodPost, url, body)
	if err != nil {
		logger.Warn("RMM script request build failed: %v", err)
		showTextWindow(rmmScriptWindowTitle, "Could not build the script request.")
		return
	}
	resp, err := trmmHTTPClient.Do(req)
	if err != nil {
		logger.Warn("RMM script request failed: %v", err)
		showTextWindow(rmmScriptWindowTitle, "Could not contact MyPortal to start the script.")
		return
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	logger.Info("RMM script request received HTTP %d for script_id=%d", resp.StatusCode, node.ScriptID)
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		logger.Warn("RMM script request HTTP %d: %s", resp.StatusCode, strings.TrimSpace(string(data)))
		showTextWindow(rmmScriptWindowTitle, rmmScriptErrorMessage(resp.StatusCode, data))
		return
	}
	var result struct {
		Message string `json:"message"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		logger.Debug("RMM script response decode failed: %v", err)
	}
	message := strings.TrimSpace(result.Message)
	if message == "" {
		message = label + " will run on this computer shortly."
	}
	showOSNotification("Script queued", message)
}

func rmmScriptLabel(node api.MenuNode) string {
	for _, candidate := range []string{node.Label, node.ScriptName} {
		if strings.TrimSpace(candidate) != "" {
			return strings.TrimSpace(candidate)
		}
	}
	return fmt.Sprintf("Script #%d", node.ScriptID)
}

// rmmScriptErrorMessage shows MyPortal's explanation for a refused run (for
// example that the RMM agent is not installed, or the script is already
// running) and a generic message for anything else.
func rmmScriptErrorMessage(statusCode int, body []byte) string {
	if statusCode == http.StatusConflict || statusCode == http.StatusForbidden {
		var detail struct {
			Detail any `json:"detail"`
		}
		if json.Unmarshal(body, &detail) == nil {
			if text, ok := detail.Detail.(string); ok && strings.TrimSpace(text) != "" && len(text) <= 300 {
				return strings.TrimSpace(text)
			}
		}
	}
	return "MyPortal could not start the script. Please contact support."
}
