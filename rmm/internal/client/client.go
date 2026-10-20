// Package client talks to MyPortal's RMM agent API (/api/rmm/agent/...).
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/thermal"
)

// ErrUnauthorized means the token was revoked or the device was removed.
var ErrUnauthorized = errors.New("MyPortal rejected the RMM agent's token")

// ErrUnavailable means MyPortal answered 503, such as event log collection
// being switched off.
var ErrUnavailable = errors.New("MyPortal reported the service unavailable")

// Details describes the device; sent on enrolment and check-in.
type Details struct {
	Thermal              *thermal.Snapshot `json:"thermal,omitempty"`
	Hostname             string            `json:"hostname,omitempty"`
	OS                   string            `json:"os,omitempty"`
	OSVersion            string            `json:"os_version,omitempty"`
	Arch                 string            `json:"arch,omitempty"`
	AgentVersion         string            `json:"agent_version,omitempty"`
	Shells               []string          `json:"shells,omitempty"`
	RunAnywaySupported   bool              `json:"run_anyway_supported"`
	RunAsUserSupported   bool              `json:"run_as_user_supported"`
	ScriptFilesSupported bool              `json:"script_files_supported"`
}

// Enrolment is MyPortal's answer to a successful enrolment.
type Enrolment struct {
	AgentUID        string `json:"agent_uid"`
	AuthToken       string `json:"auth_token"`
	PollWaitSeconds int    `json:"poll_wait_seconds"`
}

// Job is one script run to execute.
type Job struct {
	RunAnyway      bool   `json:"run_anyway"`
	ID             int64  `json:"id"`
	Name           string `json:"name"`
	Path           string `json:"path"`
	Language       string `json:"language"`
	Script         string `json:"script"`
	SHA256         string `json:"sha256"`
	TimeoutSeconds int    `json:"timeout_seconds"`
	// RunAs is "system" (the default) or "user" for the logged-in user.
	RunAs          string                 `json:"run_as"`
	Parameters     map[string]interface{} `json:"parameters"`
	ParameterOrder []string               `json:"parameter_order"`
	Env            map[string]string      `json:"env"`
	Files          []ScriptFile           `json:"files"`
}

// ScriptFile identifies a file version and its absolute endpoint destination.
type ScriptFile struct {
	SourcePath  string `json:"source_path"`
	Destination string `json:"destination"`
	MD5         string `json:"md5"`
	SHA256      string `json:"sha256"`
	Size        int64  `json:"size"`
}

// DownloadRunFile streams an assigned file through the authenticated portal.
func (c *Client) DownloadRunFile(ctx context.Context, runID int64, index int, out io.Writer) error {
	path := fmt.Sprintf("/api/rmm/agent/runs/%d/files/%d", runID, index)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.BaseURL+path, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.Token)
	req.Header.Set("User-Agent", "MyPortal-RMM-Agent")
	// File downloads must never redirect the device token to another service.
	httpClient := *c.HTTP
	httpClient.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
	resp, err := httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusUnauthorized {
		return ErrUnauthorized
	}
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("file download: HTTP %d", resp.StatusCode)
	}
	_, err = io.Copy(out, io.LimitReader(resp.Body, (50<<20)+1))
	return err
}

// CustomValue is a value the script asked MyPortal to store.
type CustomValue struct {
	Scope string `json:"scope"`
	Name  string `json:"name"`
	Value string `json:"value"`
}

// Result is reported when a job finishes.
type Result struct {
	ExitCode     *int          `json:"exit_code"`
	Stdout       string        `json:"stdout"`
	Stderr       string        `json:"stderr"`
	TimedOut     bool          `json:"timed_out"`
	Error        string        `json:"error,omitempty"`
	CustomValues []CustomValue `json:"custom_values"`
}

// Client is an authenticated API client.
type Client struct {
	BaseURL string
	Token   string
	HTTP    *http.Client
}

// New returns a client for the portal at baseURL.
func New(baseURL, token string) *Client {
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		Token:   token,
		HTTP:    &http.Client{Timeout: 60 * time.Second},
	}
}

func (c *Client) do(ctx context.Context, method, path string, body, out interface{}) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, reader)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "MyPortal-RMM-Agent")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	payload, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<20))
	if resp.StatusCode == http.StatusUnauthorized {
		return ErrUnauthorized
	}
	if resp.StatusCode == http.StatusServiceUnavailable {
		return fmt.Errorf("%s %s: %w", method, path, ErrUnavailable)
	}
	if resp.StatusCode >= 400 {
		var detail struct {
			Detail interface{} `json:"detail"`
		}
		_ = json.Unmarshal(payload, &detail)
		return fmt.Errorf("%s %s: HTTP %d %v", method, path, resp.StatusCode, detail.Detail)
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(payload, out)
}

// Enrol registers this agent using the tray device's token.
func Enrol(ctx context.Context, baseURL, trayToken, agentUID string, details Details) (Enrolment, error) {
	var result Enrolment
	payload := struct {
		Details
		AgentUID string `json:"agent_uid"`
	}{details, agentUID}
	err := New(baseURL, trayToken).do(ctx, http.MethodPost, "/api/rmm/agent/enrol", payload, &result)
	return result, err
}

// Checkin reports the device details.
func (c *Client) Checkin(ctx context.Context, details Details) error {
	return c.do(ctx, http.MethodPost, "/api/rmm/agent/checkin", details, nil)
}

// Jobs long-polls for queued runs.
func (c *Client) Jobs(ctx context.Context, wait time.Duration) ([]Job, error) {
	var out struct {
		Jobs []Job `json:"jobs"`
	}
	path := fmt.Sprintf("/api/rmm/agent/jobs?wait=%d", int(wait.Seconds()))
	err := c.do(ctx, http.MethodGet, path, nil, &out)
	return out.Jobs, err
}

// Started marks a run as running.
func (c *Client) Started(ctx context.Context, runID int64) error {
	return c.do(ctx, http.MethodPost, fmt.Sprintf("/api/rmm/agent/runs/%d/started", runID), struct{}{}, nil)
}

// Output is a cumulative snapshot; retries replace rather than duplicate text.
type Output struct {
	Stdout string `json:"stdout"`
	Stderr string `json:"stderr"`
}

func (c *Client) ReportOutput(ctx context.Context, runID int64, output Output) error {
	return c.do(ctx, http.MethodPost, fmt.Sprintf("/api/rmm/agent/runs/%d/output", runID), output, nil)
}

// Report sends a run's result.
func (c *Client) Report(ctx context.Context, runID int64, result Result) error {
	if result.CustomValues == nil {
		result.CustomValues = []CustomValue{}
	}
	return c.do(ctx, http.MethodPost, fmt.Sprintf("/api/rmm/agent/runs/%d/result", runID), result, nil)
}

// EventLogConfig says which Windows event logs to collect.
type EventLogConfig struct {
	Enabled              bool     `json:"enabled"`
	Logs                 []string `json:"logs"`
	IntervalSeconds      int      `json:"interval_seconds"`
	MaxBatch             int      `json:"max_batch"`
	InitialLookbackHours int      `json:"initial_lookback_hours"`
}

// EventLogEntry is one event log record sent to MyPortal.
type EventLogEntry struct {
	LogName     string    `json:"log_name"`
	RecordID    uint64    `json:"record_id"`
	EventID     uint32    `json:"event_id"`
	Level       int       `json:"level"`
	Provider    string    `json:"provider,omitempty"`
	Computer    string    `json:"computer,omitempty"`
	Message     string    `json:"message,omitempty"`
	TimeCreated time.Time `json:"time_created"`
}

// EventLogConfig asks which event logs to collect.
func (c *Client) EventLogConfig(ctx context.Context) (EventLogConfig, error) {
	var out EventLogConfig
	err := c.do(ctx, http.MethodGet, "/api/rmm/agent/event-logs/config", nil, &out)
	return out, err
}

// SendEventLogs sends a batch of entries; ErrUnavailable means collection is off.
func (c *Client) SendEventLogs(ctx context.Context, entries []EventLogEntry) error {
	payload := struct {
		Entries []EventLogEntry `json:"entries"`
	}{entries}
	return c.do(ctx, http.MethodPost, "/api/rmm/agent/event-logs", payload, nil)
}

// CheckService is a service a check watches, and what the agent does when it
// is not in the expected state: "none", "start", "restart" or "stop".
type CheckService struct {
	Name     string `json:"name"`
	Expected string `json:"expected"`
	Fix      string `json:"fix"`
}

// CheckConfig says what to measure for MyPortal's checks.
type CheckConfig struct {
	Enabled         bool           `json:"enabled"`
	IntervalSeconds int            `json:"interval_seconds"`
	ConfigSeconds   int            `json:"config_seconds"`
	CPU             bool           `json:"cpu"`
	Memory          bool           `json:"memory"`
	Disks           bool           `json:"disks"`
	Network         bool           `json:"network"`
	Pings           []string       `json:"pings"`
	Services        []CheckService `json:"services"`
	Files           []string       `json:"files"`
	Processes       []string       `json:"processes"`
}

// DiskUsage is one fixed drive or mounted filesystem.
type DiskUsage struct {
	Name       string `json:"name"`
	TotalBytes uint64 `json:"total_bytes"`
	FreeBytes  uint64 `json:"free_bytes"`
}

// MemoryUsage is physical memory.
type MemoryUsage struct {
	TotalBytes     uint64 `json:"total_bytes"`
	AvailableBytes uint64 `json:"available_bytes"`
}

// InterfaceRate is a network interface's throughput in bytes a second.
type InterfaceRate struct {
	Name  string  `json:"name"`
	RxBps float64 `json:"rx_bps"`
	TxBps float64 `json:"tx_bps"`
}

// PingResult is the outcome of pinging one target.
type PingResult struct {
	Target   string   `json:"target"`
	Sent     int      `json:"sent"`
	Received int      `json:"received"`
	AvgMs    *float64 `json:"avg_ms,omitempty"`
	Error    string   `json:"error,omitempty"`
}

// ServiceStatus is a watched service's state after any fix the agent made.
type ServiceStatus struct {
	Name   string `json:"name"`
	Status string `json:"status"`
	Action string `json:"action,omitempty"`
	Error  string `json:"error,omitempty"`
}

// FileStatus is whether a watched path exists. A path with * or ? matches
// any file or folder; Found is the first match.
type FileStatus struct {
	Path        string `json:"path"`
	Exists      bool   `json:"exists"`
	Found       string `json:"found,omitempty"`
	Kind        string `json:"kind,omitempty"`
	SizeBytes   *int64 `json:"size_bytes,omitempty"`
	ModifiedUTC string `json:"modified_utc,omitempty"`
	Error       string `json:"error,omitempty"`
}

// ProcessStatus is how many processes with a watched name are running.
type ProcessStatus struct {
	Name  string `json:"name"`
	Count int    `json:"count"`
	Error string `json:"error,omitempty"`
}

// CheckReport is one round of measurements. A nil field was not measured.
type CheckReport struct {
	CPUPercent *float64        `json:"cpu_percent"`
	Memory     *MemoryUsage    `json:"memory"`
	Disks      []DiskUsage     `json:"disks"`
	Network    []InterfaceRate `json:"network"`
	Pings      []PingResult    `json:"pings"`
	Services   []ServiceStatus `json:"services"`
	Files      []FileStatus    `json:"files"`
	Processes  []ProcessStatus `json:"processes"`
}

// GetCheckConfig asks what to measure for checks.
func (c *Client) GetCheckConfig(ctx context.Context) (CheckConfig, error) {
	var out CheckConfig
	err := c.do(ctx, http.MethodGet, "/api/rmm/agent/checks/config", nil, &out)
	return out, err
}

// SendCheckReport sends one round of measurements.
func (c *Client) SendCheckReport(ctx context.Context, report CheckReport) error {
	if report.Pings == nil {
		report.Pings = []PingResult{}
	}
	if report.Services == nil {
		report.Services = []ServiceStatus{}
	}
	if report.Files == nil {
		report.Files = []FileStatus{}
	}
	if report.Processes == nil {
		report.Processes = []ProcessStatus{}
	}
	return c.do(ctx, http.MethodPost, "/api/rmm/agent/checks/report", report, nil)
}
