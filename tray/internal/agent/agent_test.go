package agent

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"
)

func testAgent(collect func(context.Context, []LogSpec) (string, []string, error), llm func(context.Context, LLMConfig, string, string) (string, error)) *Agent {
	return &Agent{
		CollectLogs: collect,
		CallLLM:     llm,
		Window:      time.Hour,
	}
}

func TestRunSuccess(t *testing.T) {
	a := testAgent(
		func(context.Context, []LogSpec) (string, []string, error) {
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
		func(context.Context, []LogSpec) (string, []string, error) {
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
		func(context.Context, []LogSpec) (string, []string, error) {
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
		func(context.Context, []LogSpec) (string, []string, error) {
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
		func(context.Context, []LogSpec) (string, []string, error) {
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
	var captured []LogSpec
	a := &Agent{
		CollectLogs: func(_ context.Context, specs []LogSpec) (string, []string, error) {
			captured = specs
			return "", nil, nil
		},
		CallLLM: func(context.Context, LLMConfig, string, string) (string, error) { return "", nil },
	}
	if _, err := a.Run(context.Background(), Request{Endpoint: "h"}); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(captured) != len(defaultPlatformSources) {
		t.Fatalf("specs = %v, want one per default source %v", captured, defaultPlatformSources)
	}
	for _, spec := range captured {
		if spec.Window != DefaultWindow {
			t.Errorf("window for %s = %v, want default %v", spec.Source, spec.Window, DefaultWindow)
		}
	}
}

func TestRunCollectOnlyReadsRequestedSourcesAndSkipsLLM(t *testing.T) {
	var captured []LogSpec
	var progress []string
	llmCalled := false
	a := &Agent{
		CollectLogs: func(_ context.Context, specs []LogSpec) (string, []string, error) {
			captured = specs
			return "### System Log\n2024-05-01 [Error] disk", []string{"System"}, nil
		},
		AllowedSource: func(source string) bool { return source == "System" || source == "Setup" },
		CallLLM: func(context.Context, LLMConfig, string, string) (string, error) {
			llmCalled = true
			return "should not be used", nil
		},
		OnProgress: func(stage, message string) { progress = append(progress, stage+": "+message) },
	}
	req := Request{
		Endpoint: "h",
		Mode:     ModeCollectLogs,
		LLM:      LLMConfig{BaseURL: "http://x", Model: "m"},
		LogRequests: []LogRequest{
			{Source: "System", Reason: "Disk errors were reported", Hours: 6},
			{Source: "Setup", Reason: "Check the last update", Hours: 500},
			{Source: "C:/secrets.txt", Reason: "not allowed"},
		},
	}
	res, err := a.Run(context.Background(), req)
	if err == nil || !strings.Contains(err.Error(), "C:/secrets.txt") {
		t.Fatalf("expected an error naming the refused source, got %v", err)
	}
	if llmCalled {
		t.Error("collect-only mode must not call the LLM")
	}
	if res.Guidance != "" {
		t.Errorf("Guidance = %q, want empty", res.Guidance)
	}
	if len(res.LogBundle) == 0 {
		t.Error("expected a log bundle")
	}
	want := []LogSpec{{"System", 6 * time.Hour}, {"Setup", MaxRequestWindow}}
	if len(captured) != len(want) {
		t.Fatalf("specs = %v, want %v", captured, want)
	}
	for i := range want {
		if captured[i] != want[i] {
			t.Errorf("spec %d = %v, want %v", i, captured[i], want[i])
		}
	}
	if len(progress) == 0 || !strings.Contains(progress[0], "Disk errors were reported") {
		t.Errorf("progress should name the reason for each source: %v", progress)
	}
}

func TestResolveLogRequestsDefaultsAndDedupes(t *testing.T) {
	specs, refused := ResolveLogRequests([]LogRequest{
		{Source: " System ", Hours: 0},
		{Source: "System", Hours: 12},
		{Source: "Bogus"},
	}, isWindowsLogSource)
	if len(refused) != 1 || refused[0] != "Bogus" {
		t.Errorf("refused = %v", refused)
	}
	if len(specs) != 1 || specs[0].Source != "System" || specs[0].Window != DefaultWindow {
		t.Errorf("specs = %v", specs)
	}
}

func TestLogSourceAllowlistsAreQuoteFree(t *testing.T) {
	for _, s := range WindowsLogSources {
		if strings.ContainsAny(s, "'\"`$") {
			t.Errorf("windows source %q must not contain quote or expansion characters", s)
		}
	}
	for key := range MacOSLogPredicates {
		if !strings.HasPrefix(key, "macos:") {
			t.Errorf("macOS source %q must use the macos: prefix", key)
		}
	}
}

// gunzip reads a .tar.gz log bundle and returns its files' contents joined.
func gunzip(b []byte) (string, error) {
	files, err := untar(b)
	if err != nil {
		return "", err
	}
	var out strings.Builder
	for _, f := range files {
		out.WriteString(f.Content)
	}
	return out.String(), nil
}

func untar(b []byte) ([]LogFile, error) {
	zr, err := gzip.NewReader(bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	defer zr.Close()
	tr := tar.NewReader(zr)
	var files []LogFile
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			return files, nil
		}
		if err != nil {
			return nil, err
		}
		data, err := io.ReadAll(tr)
		if err != nil {
			return nil, err
		}
		files = append(files, LogFile{Name: hdr.Name, Content: string(data)})
	}
}

func TestRunReportsProgress(t *testing.T) {
	a := testAgent(
		func(context.Context, []LogSpec) (string, []string, error) {
			return "some log", []string{"System"}, nil
		},
		func(context.Context, LLMConfig, string, string) (string, error) {
			return "guidance", nil
		},
	)
	var stages []string
	a.OnProgress = func(stage, message string) {
		if message == "" {
			t.Errorf("stage %q reported an empty message", stage)
		}
		stages = append(stages, stage)
	}
	req := Request{Endpoint: "h", Prompt: "p", LLM: LLMConfig{BaseURL: "http://x", Model: "m"}}
	if _, err := a.Run(context.Background(), req); err != nil {
		t.Fatalf("Run returned error: %v", err)
	}
	want := []string{StageCollectingLogs, StageLogsCollected, StageAnalysing}
	if strings.Join(stages, ",") != strings.Join(want, ",") {
		t.Errorf("stages = %v, want %v", stages, want)
	}
}

func TestRunProgressSkipsAnalysingWithoutModel(t *testing.T) {
	a := testAgent(
		func(context.Context, []LogSpec) (string, []string, error) {
			return "", nil, errors.New("boom")
		},
		nil,
	)
	var stages []string
	a.OnProgress = func(stage, _ string) { stages = append(stages, stage) }
	_, _ = a.Run(context.Background(), Request{Endpoint: "h"})
	for _, s := range stages {
		if s == StageAnalysing {
			t.Error("analysing should not be reported when no model is configured")
		}
	}
	if len(stages) != 2 {
		t.Errorf("stages = %v, want collecting + collected", stages)
	}
}
