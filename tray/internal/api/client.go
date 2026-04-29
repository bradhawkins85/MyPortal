// Package api provides an HTTP + WebSocket client for talking to the
// MyPortal server from the tray service.
package api

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

// Client wraps the MyPortal REST + WebSocket API for tray devices.
type Client struct {
	baseURL   string
	authToken string
	deviceUID string
	http      *http.Client
	mu        sync.RWMutex
}

// New creates a new API client pointing at portalURL.
func New(portalURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(portalURL, "/"),
		http:    &http.Client{Timeout: 30 * time.Second},
	}
}

// SetAuth configures the per-device auth token after enrolment.
func (c *Client) SetAuth(deviceUID, authToken string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.deviceUID = deviceUID
	c.authToken = authToken
}

// DeviceUID returns the stored device UID.
func (c *Client) DeviceUID() string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.deviceUID
}

// EnrolRequest mirrors the server's TrayEnrolRequest schema.
type EnrolRequest struct {
	InstallToken  string  `json:"install_token"`
	DeviceUID     string  `json:"device_uid,omitempty"`
	OS            string  `json:"os"`
	OSVersion     string  `json:"os_version,omitempty"`
	Hostname      string  `json:"hostname,omitempty"`
	SerialNumber  string  `json:"serial_number,omitempty"`
	AgentVersion  string  `json:"agent_version,omitempty"`
	ConsoleUser   string  `json:"console_user,omitempty"`
}

// EnrolResponse mirrors the server's TrayEnrolResponse schema.
type EnrolResponse struct {
	DeviceUID           string `json:"device_uid"`
	AuthToken           string `json:"auth_token"`
	CompanyID           *int   `json:"company_id,omitempty"`
	AssetID             *int   `json:"asset_id,omitempty"`
	PollIntervalSeconds int    `json:"poll_interval_seconds"`
}

// Enrol exchanges the install token for a long-lived auth token.
func (c *Client) Enrol(ctx context.Context, req EnrolRequest) (*EnrolResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	resp, err := c.post(ctx, "/api/tray/enrol", body, false)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("enrol: HTTP %d", resp.StatusCode)
	}
	var out EnrolResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	c.SetAuth(out.DeviceUID, out.AuthToken)
	return &out, nil
}

// MenuNode mirrors the server's TrayMenuNode schema.
type MenuNode struct {
	Type     string      `json:"type"`
	Label    string      `json:"label,omitempty"`
	URL      string      `json:"url,omitempty"`
	Name     string      `json:"name,omitempty"`
	Text     string      `json:"text,omitempty"`
	Children []*MenuNode `json:"children,omitempty"`
}

// ConfigResponse mirrors the server's TrayConfigResponse schema.
type ConfigResponse struct {
	Version          int        `json:"version"`
	Menu             []MenuNode `json:"menu"`
	DisplayText      string     `json:"display_text,omitempty"`
	BrandingIconURL  string     `json:"branding_icon_url,omitempty"`
	EnvAllowlist     []string   `json:"env_allowlist"`
	ChatEnabled      bool       `json:"chat_enabled"`
}

// GetConfig fetches the resolved menu configuration for this device.
func (c *Client) GetConfig(ctx context.Context) (*ConfigResponse, error) {
	resp, err := c.get(ctx, "/api/tray/config")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("config: HTTP %d", resp.StatusCode)
	}
	var out ConfigResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// HeartbeatRequest mirrors the server's TrayHeartbeatRequest schema.
type HeartbeatRequest struct {
	ConsoleUser  string `json:"console_user,omitempty"`
	AgentVersion string `json:"agent_version,omitempty"`
	LastIP       string `json:"last_ip,omitempty"`
}

// Heartbeat sends a liveness ping and updates device facts on the server.
func (c *Client) Heartbeat(ctx context.Context, req HeartbeatRequest) error {
	body, err := json.Marshal(req)
	if err != nil {
		return err
	}
	resp, err := c.post(ctx, "/api/tray/heartbeat", body, true)
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("heartbeat: HTTP %d", resp.StatusCode)
	}
	return nil
}

// VersionResponse mirrors the server's TrayVersionResponse schema.
type VersionResponse struct {
	Version     string `json:"version"`
	DownloadURL string `json:"download_url,omitempty"`
	Required    bool   `json:"required"`
}

// GetVersion checks if a newer installer version is available.
func (c *Client) GetVersion(ctx context.Context) (*VersionResponse, error) {
	resp, err := c.get(ctx, "/api/tray/version")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("version: HTTP %d", resp.StatusCode)
	}
	var out VersionResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// UploadDiagnostics zips logDir and uploads it to the server.
func (c *Client) UploadDiagnostics(ctx context.Context, logDir string) error {
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)

	err := filepath.Walk(logDir, func(path string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(logDir, path)
		w, err := zw.Create(rel)
		if err != nil {
			return err
		}
		f, err := os.Open(path)
		if err != nil {
			return err
		}
		defer f.Close()
		// Cap individual file at 5 MB.
		_, err = io.Copy(w, io.LimitReader(f, 5*1024*1024))
		return err
	})
	if err != nil {
		return fmt.Errorf("diagnostics: zip: %w", err)
	}
	if err := zw.Close(); err != nil {
		return err
	}

	uid := c.DeviceUID()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		c.baseURL+"/api/tray/"+uid+"/diagnostics", &buf)
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/zip")
	c.setAuthHeader(req)

	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		return fmt.Errorf("diagnostics: HTTP %d", resp.StatusCode)
	}
	return nil
}

// ConnectWS dials the WebSocket and returns the connection.
// The caller is responsible for reading/writing and closing.
func (c *Client) ConnectWS(ctx context.Context) (*websocket.Conn, error) {
	c.mu.RLock()
	uid := c.deviceUID
	tok := c.authToken
	c.mu.RUnlock()

	rawURL := c.baseURL + "/ws/tray/" + uid
	u, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	// Replace http(s) with ws(s).
	switch u.Scheme {
	case "https":
		u.Scheme = "wss"
	default:
		u.Scheme = "ws"
	}

	hdr := http.Header{}
	hdr.Set("Authorization", "Bearer "+tok)

	dialer := websocket.DefaultDialer
	conn, _, err := dialer.DialContext(ctx, u.String(), hdr)
	if err != nil {
		return nil, err
	}
	return conn, nil
}

// -----------------------------------------------------------------
// helpers
// -----------------------------------------------------------------

func (c *Client) get(ctx context.Context, path string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return nil, err
	}
	c.setAuthHeader(req)
	return c.http.Do(req)
}

func (c *Client) post(ctx context.Context, path string, body []byte, auth bool) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path,
		bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if auth {
		c.setAuthHeader(req)
	}
	return c.http.Do(req)
}

func (c *Client) setAuthHeader(req *http.Request) {
	c.mu.RLock()
	tok := c.authToken
	c.mu.RUnlock()
	if tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
}
