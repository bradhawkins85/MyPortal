package client

import (
	"context"
	"encoding/json"
	"net/http"
)

// ChocolateySources contains company source policy, including private credentials.
// Never log it or persist it in the agent's state files.
type ChocolateySources struct {
	Script  string          `json:"script"`
	Sources json.RawMessage `json:"sources"`
}

func (c *Client) ChocolateySources(ctx context.Context) (ChocolateySources, error) {
	var out ChocolateySources
	err := c.do(ctx, http.MethodGet, "/api/rmm/agent/chocolatey/sources", nil, &out)
	return out, err
}
