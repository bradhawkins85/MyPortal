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
)

// ErrUnauthorized means the token was revoked or the device was removed.
var ErrUnauthorized = errors.New("MyPortal rejected the RMM agent's token")

// Details describes the device; sent on enrolment and check-in.
type Details struct {
	Hostname     string   `json:"hostname,omitempty"`
	OS           string   `json:"os,omitempty"`
	OSVersion    string   `json:"os_version,omitempty"`
	Arch         string   `json:"arch,omitempty"`
	AgentVersion string   `json:"agent_version,omitempty"`
	Shells       []string `json:"shells,omitempty"`
}

// Enrolment is MyPortal's answer to a successful enrolment.
type Enrolment struct {
	AgentUID        string `json:"agent_uid"`
	AuthToken       string `json:"auth_token"`
	PollWaitSeconds int    `json:"poll_wait_seconds"`
}

// Job is one script run to execute.
type Job struct {
	ID             int64                  `json:"id"`
	Name           string                 `json:"name"`
	Path           string                 `json:"path"`
	Language       string                 `json:"language"`
	Script         string                 `json:"script"`
	SHA256         string                 `json:"sha256"`
	TimeoutSeconds int                    `json:"timeout_seconds"`
	Parameters     map[string]interface{} `json:"parameters"`
	ParameterOrder []string               `json:"parameter_order"`
	Env            map[string]string      `json:"env"`
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

// Report sends a run's result.
func (c *Client) Report(ctx context.Context, runID int64, result Result) error {
	if result.CustomValues == nil {
		result.CustomValues = []CustomValue{}
	}
	return c.do(ctx, http.MethodPost, fmt.Sprintf("/api/rmm/agent/runs/%d/result", runID), result, nil)
}
