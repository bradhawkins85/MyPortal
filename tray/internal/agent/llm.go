package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// chatMessage / llmRequest / llmResponse mirror the OpenAI-compatible
// /v1/chat/completions schema. Ollama serves the same shape, so one client
// covers both local and hosted providers.
type chatMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type llmRequest struct {
	Model    string        `json:"model"`
	Messages []chatMessage `json:"messages"`
}

type llmResponse struct {
	Choices []struct {
		Message chatMessage `json:"message"`
	} `json:"choices"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

// Client is a thin OpenAI-compatible chat client.
type Client struct {
	HTTP *http.Client
}

// newLLMClient returns a Client with a generous timeout, because local models
// can be slow on first run.
func newLLMClient() *Client {
	return &Client{HTTP: &http.Client{Timeout: 5 * time.Minute}}
}

// Chat sends a single system+user turn and returns the assistant's reply.
func (c *Client) Chat(ctx context.Context, cfg LLMConfig, system, user string) (string, error) {
	base := strings.TrimRight(strings.TrimSpace(cfg.BaseURL), "/")
	if base == "" {
		return "", fmt.Errorf("llm base URL is empty")
	}
	if strings.TrimSpace(cfg.Model) == "" {
		return "", fmt.Errorf("llm model is empty")
	}

	body, err := json.Marshal(llmRequest{
		Model: cfg.Model,
		Messages: []chatMessage{
			{Role: "system", Content: system},
			{Role: "user", Content: user},
		},
	})
	if err != nil {
		return "", err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, base+"/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	if cfg.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+cfg.APIKey)
	}

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	// Bound the response so a misbehaving endpoint cannot exhaust memory.
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		return "", err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return "", fmt.Errorf("llm returned HTTP %d: %s", resp.StatusCode, truncateForError(data))
	}

	var out llmResponse
	if err := json.Unmarshal(data, &out); err != nil {
		return "", fmt.Errorf("decode llm response: %w", err)
	}
	if out.Error != nil && out.Error.Message != "" {
		return "", fmt.Errorf("llm error: %s", out.Error.Message)
	}
	if len(out.Choices) == 0 {
		return "", fmt.Errorf("llm returned no choices")
	}
	return out.Choices[0].Message.Content, nil
}

// truncateForError keeps an error message short enough to log or surface.
func truncateForError(b []byte) string {
	s := strings.TrimSpace(string(b))
	const limit = 512
	if len(s) > limit {
		return s[:limit] + "…"
	}
	return s
}
