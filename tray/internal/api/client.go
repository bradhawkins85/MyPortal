// Package api provides an HTTP + WebSocket client for talking to the
// MyPortal server from the tray service.
package api

import (
	"archive/zip"
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
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
	InstallToken string `json:"install_token"`
	DeviceUID    string `json:"device_uid,omitempty"`
	OS           string `json:"os"`
	OSVersion    string `json:"os_version,omitempty"`
	Hostname     string `json:"hostname,omitempty"`
	SerialNumber string `json:"serial_number,omitempty"`
	AgentVersion string `json:"agent_version,omitempty"`
	ConsoleUser  string `json:"console_user,omitempty"`
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
	Type       string      `json:"type"`
	Label      string      `json:"label,omitempty"`
	URL        string      `json:"url,omitempty"`
	Name       string      `json:"name,omitempty"`
	Text       string      `json:"text,omitempty"`
	Color      string      `json:"color,omitempty"`
	ScriptID   int         `json:"script_id,omitempty"`
	ScriptName string      `json:"script_name,omitempty"`
	Children   []*MenuNode `json:"children,omitempty"`
}

// ConfigResponse mirrors the server's TrayConfigResponse schema.
type ConfigResponse struct {
	Version             int        `json:"version"`
	Menu                []MenuNode `json:"menu"`
	DisplayText         string     `json:"display_text,omitempty"`
	BrandingIconURL     string     `json:"branding_icon_url,omitempty"`
	BrandingDisplayName string     `json:"branding_display_name,omitempty"`
	EnvAllowlist        []string   `json:"env_allowlist"`
	ChatEnabled         bool       `json:"chat_enabled"`
	// ChatClientMode controls how the tray opens chat windows.
	// "" or "app" (default): try dedicated chat shell, then browser app-mode, then
	// fall back to the default browser.
	// "browser": always open in the default system browser (legacy behaviour).
	// "shell": require the dedicated chat shell; log a warning if absent rather
	// than falling back to the browser.
	ChatClientMode             string `json:"chat_client_mode,omitempty"`
	NetworkScannerEnabled      bool   `json:"network_scanner_enabled"`
	NetworkScanIntervalMinutes int    `json:"network_scan_interval_minutes"`
}

type NetworkHost struct {
	IPAddress  string `json:"ip_address"`
	MACAddress string `json:"mac_address,omitempty"`
	Hostname   string `json:"hostname,omitempty"`
	Vendor     string `json:"vendor,omitempty"`
	OSDetails  string `json:"os_details,omitempty"`
	OpenPorts  string `json:"open_ports,omitempty"`
}

// GetWANIP obtains the public address using the whoami-compatible source
// configured by the portal. The source is called by this agent so it observes
// the WAN address of the network being scanned.
func (c *Client) GetWANIP(ctx context.Context) (string, error) {
	resp, err := c.get(ctx, "/api/tray/wan-ip")
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("WAN IP lookup: HTTP %d", resp.StatusCode)
	}
	var out struct {
		WANIP       string `json:"wan_ip"`
		SourceURL   string `json:"source_url"`
		SourceField string `json:"source_field"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return "", err
	}
	if out.SourceURL != "" {
		return c.getWANIPFromSource(ctx, out.SourceURL, out.SourceField)
	}
	if net.ParseIP(out.WANIP) == nil {
		return "", fmt.Errorf("WAN IP lookup returned an invalid address")
	}
	return out.WANIP, nil
}

func (c *Client) getWANIPFromSource(ctx context.Context, sourceURL, sourceField string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, sourceURL, nil)
	if err != nil {
		return "", fmt.Errorf("WAN IP source request: %w", err)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return "", fmt.Errorf("WAN IP source request: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("WAN IP source: HTTP %d", resp.StatusCode)
	}

	scanner := bufio.NewScanner(io.LimitReader(resp.Body, 1<<20))
	for scanner.Scan() {
		name, value, found := strings.Cut(scanner.Text(), ":")
		if !found || !strings.EqualFold(strings.TrimSpace(name), strings.TrimSpace(sourceField)) {
			continue
		}
		// Forwarded fields can contain a comma-separated proxy chain. The first
		// valid address is the original client and therefore the scanner WAN IP.
		for _, candidate := range strings.Split(value, ",") {
			candidate = strings.TrimSpace(candidate)
			if net.ParseIP(candidate) != nil {
				return candidate, nil
			}
		}
		return "", fmt.Errorf("WAN IP source field %q did not contain a valid address", sourceField)
	}
	if err := scanner.Err(); err != nil {
		return "", fmt.Errorf("read WAN IP source: %w", err)
	}
	return "", fmt.Errorf("WAN IP source field %q was not found", sourceField)
}

func (c *Client) UploadNetworkScan(ctx context.Context, wanIP string, subnets []string, hosts []NetworkHost) error {
	body, err := json.Marshal(map[string]interface{}{"wan_ip": wanIP, "subnets": subnets, "hosts": hosts})
	if err != nil {
		return err
	}
	resp, err := c.post(ctx, "/api/tray/network-scan", body, true)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("network scan upload: HTTP %d", resp.StatusCode)
	}
	return nil
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

// DefenderPolicy is the effective Defender configuration for this device.
// A disabled policy is also returned when the company has not opted in.
type DefenderPolicy struct {
	Enabled bool `json:"enabled"`
}

// DefenderStatus is the protection state collected by the Windows service.
type DefenderStatus struct {
	AntivirusEnabled          bool                   `json:"antivirus_enabled"`
	RealtimeProtectionEnabled bool                   `json:"realtime_protection_enabled"`
	TamperProtectionEnabled   bool                   `json:"tamper_protection_enabled"`
	SignaturesUpdatedAt       *time.Time             `json:"signatures_updated_at,omitempty"`
	LastScanAt                *time.Time             `json:"last_scan_at,omitempty"`
	ScanHistory               []DefenderScan         `json:"scan_history"`
	HealthStatus              string                 `json:"health_status"`
	Details                   map[string]interface{} `json:"details"`
}

// DefenderScan describes a recent scan reported by Microsoft Defender.
type DefenderScan struct {
	ScanType        string     `json:"scan_type"`
	StartedAt       *time.Time `json:"started_at,omitempty"`
	CompletedAt     *time.Time `json:"completed_at,omitempty"`
	DurationSeconds *int64     `json:"duration_seconds,omitempty"`
	Status          string     `json:"status"`
}

// GetDefenderPolicy checks whether Defender reporting is enabled. The server
// intentionally returns 404 for devices whose company has not opted in.
func (c *Client) GetDefenderPolicy(ctx context.Context) (*DefenderPolicy, error) {
	resp, err := c.get(ctx, "/api/tray/defender/policy")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return &DefenderPolicy{Enabled: false}, nil
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("defender policy: HTTP %d", resp.StatusCode)
	}
	var out DefenderPolicy
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// ReportDefenderStatus uploads the latest endpoint protection state.
func (c *Client) ReportDefenderStatus(ctx context.Context, status DefenderStatus) error {
	body, err := json.Marshal(status)
	if err != nil {
		return err
	}
	resp, err := c.post(ctx, "/api/tray/defender/status", body, true)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("defender status: HTTP %d", resp.StatusCode)
	}
	return nil
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
// The current OS is sent in the X-Tray-OS header so the server can return
// a platform-specific installer, and the device auth token is included so
// the server can apply per-device rollout bucketing.
func (c *Client) GetVersion(ctx context.Context) (*VersionResponse, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/api/tray/version", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-Tray-OS", runtime.GOOS)
	c.setAuthHeader(req)
	resp, err := c.http.Do(req)
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

// ChatTokenResponse mirrors the server's TrayChatTokenResponse schema.
type ChatTokenResponse struct {
	Token     string `json:"token"`
	ExpiresIn int    `json:"expires_in"`
	ChatURL   string `json:"chat_url"`
}

// TicketQuestionCondition mirrors one conditional visibility rule.
type TicketQuestionCondition struct {
	ParentQuestionID int    `json:"parent_question_id"`
	Operator         string `json:"operator"`
	ExpectedValue    string `json:"expected_value"`
}

// TicketQuestion mirrors one dynamic intake question returned by
// GET /api/tray/ticket-questions.
type TicketQuestion struct {
	ID          int                       `json:"id"`
	Scope       string                    `json:"scope"`
	FieldType   string                    `json:"field_type"`
	Label       string                    `json:"label"`
	Placeholder string                    `json:"placeholder,omitempty"`
	IsRequired  bool                      `json:"is_required"`
	Options     []string                  `json:"options"`
	SortOrder   int                       `json:"sort_order"`
	Conditions  []TicketQuestionCondition `json:"conditions"`
}

// TicketQuestionsResponse mirrors TrayTicketQuestionsResponse.
type TicketQuestionsResponse struct {
	Questions []TicketQuestion `json:"questions"`
}

// GetTicketQuestions fetches the dynamic intake questions for this device.
func (c *Client) GetTicketQuestions(ctx context.Context) (*TicketQuestionsResponse, error) {
	resp, err := c.get(ctx, "/api/tray/ticket-questions")
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ticket-questions: HTTP %d", resp.StatusCode)
	}
	var out TicketQuestionsResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// RequestChatToken asks the server for a short-lived one-time URL token that
// lets the popup webview open /tray/chat without requiring the user to log in.
// roomID may be 0 when the user is starting a new chat (no existing room).
func (c *Client) RequestChatToken(ctx context.Context, roomID int) (*ChatTokenResponse, error) {
	var body []byte
	if roomID > 0 {
		var err error
		body, err = json.Marshal(map[string]int{"room_id": roomID})
		if err != nil {
			return nil, err
		}
	} else {
		body = []byte("{}")
	}
	resp, err := c.post(ctx, "/api/tray/chat-token", body, true)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("chat-token: HTTP %d", resp.StatusCode)
	}
	var out ChatTokenResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	return &out, nil
}

// RunTRMMScript asks MyPortal to run a configured Tactical RMM script on this device.
func (c *Client) RunTRMMScript(ctx context.Context, scriptID int) error {
	if scriptID <= 0 {
		return fmt.Errorf("trmm-script: script id is required")
	}
	body, err := json.Marshal(map[string]int{"script_id": scriptID})
	if err != nil {
		return err
	}
	resp, err := c.post(ctx, "/api/tray/trmm-script", body, true)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK && resp.StatusCode != http.StatusAccepted {
		return fmt.Errorf("trmm-script: HTTP %d", resp.StatusCode)
	}
	return nil
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
