package agent

import (
	"strings"
	"testing"
)

func TestBuildUserPromptIncludesContext(t *testing.T) {
	req := Request{
		Endpoint: "ws-01.internal",
		Prompt:   "RDP keeps dropping",
	}
	sources := []string{"System", "Security"}
	logSample := "2024-05-01 10:00:00 [Error] RDP session ended"

	got := buildUserPrompt(req, sources, logSample)

	for _, want := range []string{
		"Endpoint: ws-01.internal",
		"Reported problem: RDP keeps dropping",
		"Log sources: System, Security",
		"2024-05-01 10:00:00 [Error] RDP session ended",
	} {
		if !strings.Contains(got, want) {
			t.Errorf("prompt missing %q;\nfull prompt:\n%s", want, got)
		}
	}
}

func TestBuildUserPromptNoLogs(t *testing.T) {
	req := Request{Endpoint: "host"}
	got := buildUserPrompt(req, nil, "")
	if !strings.Contains(got, "[no logs available]") {
		t.Errorf("expected no-logs placeholder, got:\n%s", got)
	}
}

func TestBuildUserPromptEmptyPrompt(t *testing.T) {
	req := Request{Endpoint: "host"}
	got := buildUserPrompt(req, nil, "some log line")
	if !strings.Contains(got, "(not provided)") {
		t.Errorf("expected (not provided) for empty problem, got:\n%s", got)
	}
}
