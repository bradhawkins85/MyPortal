package api

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// maxRMMAgentBytes bounds the RMM agent download; the agent is a few MB.
const maxRMMAgentBytes = 256 << 20

// RMMAgentPackage mirrors GET /api/rmm/agent/package: the RMM agent build
// MyPortal holds for this platform.
type RMMAgentPackage struct {
	FileName     string `json:"file_name"`
	Version      string `json:"version"`
	Sha256       string `json:"sha256"`
	Size         int64  `json:"size"`
	DownloadPath string `json:"download_path"`
}

// BaseURL returns the MyPortal address the client talks to.
func (c *Client) BaseURL() string {
	return c.baseURL
}

// AuthToken returns the device token, which the RMM agent enrols with.
func (c *Client) AuthToken() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.authToken
}

// GetRMMAgentPackage asks which RMM agent build to install on goos/goarch.
func (c *Client) GetRMMAgentPackage(ctx context.Context, goos, goarch string, version ...string) (*RMMAgentPackage, error) {
	query := url.Values{"os": {goos}, "arch": {goarch}}
	if len(version) > 0 && version[0] != "" {
		query.Set("version", version[0])
	}
	resp, err := c.get(ctx, "/api/rmm/agent/package?"+query.Encode())
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("rmm agent package: HTTP %d", resp.StatusCode)
	}
	var out RMMAgentPackage
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// DownloadRMMAgent writes the build at downloadPath (from
// GetRMMAgentPackage) to w. Only MyPortal's own API paths are fetched, so the
// device token never goes to another host.
func (c *Client) DownloadRMMAgent(ctx context.Context, downloadPath string, w io.Writer) error {
	if !strings.HasPrefix(downloadPath, "/api/rmm/agent/package/download?") {
		return fmt.Errorf("rmm agent download: unexpected path %q", downloadPath)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+downloadPath, nil)
	if err != nil {
		return err
	}
	c.setAuthHeader(req)
	// The shared client's 30 s limit is too short for slow links.
	resp, err := (&http.Client{Timeout: 10 * time.Minute}).Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("rmm agent download: HTTP %d", resp.StatusCode)
	}
	n, err := io.Copy(w, io.LimitReader(resp.Body, maxRMMAgentBytes+1))
	if err != nil {
		return err
	}
	if n > maxRMMAgentBytes {
		return fmt.Errorf("rmm agent download: larger than %d bytes", maxRMMAgentBytes)
	}
	return nil
}

// PostRMMAgentInstallResult reports how an install_rmm_agent command went.
func (c *Client) PostRMMAgentInstallResult(ctx context.Context, commandID int, ok bool, message string) error {
	body, err := json.Marshal(map[string]any{"command_id": commandID, "ok": ok, "message": message})
	if err != nil {
		return err
	}
	resp, err := c.post(ctx, "/api/rmm/agent/install-result", body, true)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("rmm agent install result: HTTP %d", resp.StatusCode)
	}
	return nil
}
