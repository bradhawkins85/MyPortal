package agent

import (
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"
)

func testAgent(collect func(context.Context, time.Duration) (string, []string, error), llm func(context.Context, LLMConfig, string, string) (string, error)) *Agent {
	return &Agent{
		CollectLogs: collect,
		CallLLM:     llm,
		Window:      time.Hour,
	}
}

func TestRunSuccess(t *testing.T) {
	a := testAgent(
		func(context.Context, time.Duration) (string, []string, error) {
			return "2024-05-01 10:00:00 [Error] RDP session ended", []string{"System"}, nil
		},
		func(context.Context, LLMConfig, string, string) (string, error) {
			return "Check the RDP service.", nil
		},
	)
	req := Request{Endpoint: "ws-01", Prompt: "RDP dropping", LLM: LLMConfig{BaseURL: "http://x", Model: "m"}}
	res, err := a.Run(context.Background(), req)
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if res.Guidance != "Check the RDP service." {
		t.Errorf("Guidance = %q", res.Guidance)
	}
	if res.Endpoint != "ws-01" {
		t.Errorf("Endpoint = %q, want ws-01", res.Endpoint)
	}
	if len(res.LogBundle) == 0 {
		t.Fatal("expected a non-empty log bundle")
	}
	// The bundle must be valid gzip and contain the (unredacted) log line.
	decoded, err := gunzip(res.LogBundle)
	if err != nil {
		t.Fatalf("bundle is not valid gzip: %v", err)
	}
	if !strings.Contains(decoded, "RDP session ended") {
		t.Errorf("bundle does not contain log line: %q", decoded)
	}
}

func TestRunScrubBundle(t *testing.T) {
	a := testAgent(
		func(context.Context, time.Duration) (string, []string, error) {
			return "login api_key=supersecret123 failed", []string{"App"}, nil
		},
		func(context.Context, LLMConfig, string, string) (string, error) { return "ok", nil },
	)
	req := Request{Endpoint: "h", Prompt: "p", LLM: LLMConfig{BaseURL: "http://x", Model: "m"}}
	res, _ := a.Run(context.Background(), req)
	decoded, err := gunzip(res.LogBundle)
	if err != nil {
		t.Fatalf("bundle is not valid gzip: %v", err)
	}
	if strings.Contains(decoded, "supersecret123") {
		t.Errorf("secret leaked into bundle: %q", decoded)
	}
	if !strings.Contains(decoded, "[REDACTED]") {
		t.Errorf("expected redaction marker in bundle: %q", decoded)
	}
}

func TestRunLLMFailureStillReturnsBundle(t *testing.T) {
	a := testAgent(
		func(context.Context, time.Duration) (string, []string, error) {
			return "some log", []string{"System"}, nil
		},
		func(context.Context, LLMConfig, string, string) (string, error) {
			return "", errors.New("boom")
		},
	)
	req := Request{Endpoint: "h", Prompt: "p", LLM: LLMConfig{BaseURL: "http://x", Model: "m"}}
	res, err := a.Run(context.Background(), req)
	if err == nil {
		t.Fatal("expected an error from the failing LLM")
	}
	if res.Guidance != "" {
		t.Errorf("Guidance should be empty on LLM failure, got %q", res.Guidance)
	}
	if len(res.LogBundle) == 0 {
		t.Error("expected the log bundle to survive the LLM failure")
	}
	if !strings.Contains(err.Error(), "llm") {
		t.Errorf("error should mention llm, got %q", err)
	}
}

func TestRunNoModelSkipsLLM(t *testing.T) {
	called := false
	a := testAgent(
		func(context.Context, time.Duration) (string, []string, error) {
			return "some log", []string{"System"}, nil
		},
		func(context.Context, LLMConfig, string, string) (string, error) {
			called = true
			return "should not be called", nil
		},
	)
	req := Request{Endpoint: "h", Prompt: "p"} // no LLM.Model
	res, err := a.Run(context.Background(), req)
	if err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	if called {
		t.Error("LLM should not be called when no model is configured")
	}
	if res.Guidance != "" {
		t.Errorf("Guidance should be empty, got %q", res.Guidance)
	}
	if len(res.LogBundle) == 0 {
		t.Error("expected a log bundle")
	}
}

func TestRunLogCollectionFailureStillCallsLLM(t *testing.T) {
	llmCalls := 0
	a := testAgent(
		func(context.Context, time.Duration) (string, []string, error) {
			return "", nil, errors.New("no such log")
		},
		func(context.Context, LLMConfig, string, string) (string, error) {
			llmCalls++
			return "Try restarting.", nil
		},
	)
	req := Request{Endpoint: "h", Prompt: "p", LLM: LLMConfig{BaseURL: "http://x", Model: "m"}}
	res, err := a.Run(context.Background(), req)
	if err == nil {
		t.Fatal("expected an error from failing log collection")
	}
	if llmCalls != 1 {
		t.Errorf("LLM should still be called once, got %d", llmCalls)
	}
	if res.Guidance != "Try restarting." {
		t.Errorf("Guidance = %q", res.Guidance)
	}
	if len(res.LogBundle) != 0 {
		t.Error("expected no log bundle when collection failed")
	}
}

func TestRunDefaultWindowWhenZero(t *testing.T) {
	window := time.Duration(0)
	capturedWindow := time.Duration(0)
	a := &Agent{
		CollectLogs: func(_ context.Context, w time.Duration) (string, []string, error) {
			capturedWindow = w
			return "", nil, nil
		},
		CallLLM: func(context.Context, LLMConfig, string, string) (string, error) { return "", nil },
		Window:  window,
	}
	if _, err := a.Run(context.Background(), Request{Endpoint: "h"}); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if capturedWindow != DefaultWindow {
		t.Errorf("window = %v, want default %v", capturedWindow, DefaultWindow)
	}
}

func gunzip(b []byte) (string, error) {
	zr, err := gzip.NewReader(bytes.NewReader(b))
	if err != nil {
		return "", err
	}
	defer zr.Close()
	data, err := io.ReadAll(zr)
	if err != nil {
		return "", err
	}
	return string(data), nil
}
