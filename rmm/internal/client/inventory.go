package client

import (
	"context"
	"net/http"
)

// InventoryConfig says whether MyPortal wants a new inventory snapshot.
type InventoryConfig struct {
	Due         bool `json:"due"`
	PollSeconds int  `json:"poll_seconds"`
}

// InventoryConfig asks whether a snapshot is due: none yet, the last is
// older than MyPortal's refresh interval, or a technician asked for one.
func (c *Client) InventoryConfig(ctx context.Context) (InventoryConfig, error) {
	var out InventoryConfig
	err := c.do(ctx, http.MethodGet, "/api/rmm/agent/inventory/config", nil, &out)
	return out, err
}

// SendInventory sends a hardware and software snapshot (an
// inventory.Snapshot; any JSON value is accepted here so this package does
// not depend on the collector).
func (c *Client) SendInventory(ctx context.Context, snapshot interface{}) error {
	return c.do(ctx, http.MethodPost, "/api/rmm/agent/inventory", snapshot, nil)
}
