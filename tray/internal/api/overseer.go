package api

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// OverseerServiceState is what the overseer saw of one local agent service.
type OverseerServiceState struct {
	Service   string `json:"service"`
	Installed bool   `json:"installed"`
	Enabled   bool   `json:"enabled"`
	Running   bool   `json:"running"`
}

// OverseerAction is one recovery step the overseer took on the device.
type OverseerAction struct {
	Service    string    `json:"service"`
	Action     string    `json:"action"`
	Reason     string    `json:"reason"`
	OccurredAt time.Time `json:"occurred_at"`
	Succeeded  bool      `json:"succeeded"`
	Error      string    `json:"error,omitempty"`
}

// OverseerReport mirrors the server's TrayOverseerReport schema.
type OverseerReport struct {
	OverseerVersion string                 `json:"overseer_version"`
	Services        []OverseerServiceState `json:"services"`
	Actions         []OverseerAction       `json:"actions"`
}

// OverseerAgentStatus is how recently MyPortal heard from one agent. Ages are
// worked out on the server so the device clock does not matter.
type OverseerAgentStatus struct {
	Enrolled           bool   `json:"enrolled"`
	LastSeenAgeSeconds *int64 `json:"last_seen_age_seconds"`
}

// OverseerStatus mirrors the server's TrayOverseerStatus schema.
type OverseerStatus struct {
	Tray                 OverseerAgentStatus `json:"tray"`
	RMM                  OverseerAgentStatus `json:"rmm"`
	CheckIntervalSeconds int                 `json:"check_interval_seconds"`
}

// HTTPStatusError is returned when MyPortal answered with a non-200 status.
type HTTPStatusError struct {
	Operation string
	Status    int
}

func (e *HTTPStatusError) Error() string {
	return fmt.Sprintf("%s: HTTP %d", e.Operation, e.Status)
}

// ReportOverseer sends the overseer's report and returns MyPortal's view of
// the device's tray and RMM agent connections.
func (c *Client) ReportOverseer(ctx context.Context, report OverseerReport) (*OverseerStatus, error) {
	if report.Services == nil {
		report.Services = []OverseerServiceState{}
	}
	if report.Actions == nil {
		report.Actions = []OverseerAction{}
	}
	body, err := json.Marshal(report)
	if err != nil {
		return nil, err
	}
	resp, err := c.post(ctx, "/api/tray/overseer/report", body, true)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		return nil, &HTTPStatusError{Operation: "overseer report", Status: resp.StatusCode}
	}
	var out OverseerStatus
	if err := json.NewDecoder(io.LimitReader(resp.Body, 64<<10)).Decode(&out); err != nil {
		return nil, fmt.Errorf("overseer report: %w", err)
	}
	return &out, nil
}
