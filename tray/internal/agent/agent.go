// Package agent implements the AI troubleshooting agent that runs on a tray
// device. When the MyPortal server dispatches a "troubleshoot" command over
// the device WebSocket, the agent:
//
//  1. collects up to a rolling window of read-only endpoint logs,
//  2. redacts anything that looks like a secret or personal data,
//  3. asks the configured OpenAI-compatible LLM for remediation guidance, and
//  4. returns the guidance plus the compressed, redacted log bundle so the
//     caller can report them back to the server as an internal ticket note and
//     a staff-only attachment.
//
// The agent performs no privileged operation: it only reads local logs and
// makes a single outbound request to the LLM endpoint the server supplies.
package agent

import (
	"bytes"
	"compress/gzip"
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

// DefaultWindow is how far back the agent looks when collecting logs.
const DefaultWindow = 24 * time.Hour

// Budget constants. A small prompt keeps the LLM request under typical context
// limits and keeps the ticket note readable; the full redacted bundle is still
// attached so a technician can download it.
const (
	// maxLogSampleBytes is the maximum size of the log text embedded in the
	// prompt sent to the LLM.
	maxLogSampleBytes = 48 * 1024
	// maxGuidanceBytes caps how much LLM output is stored as a ticket note.
	maxGuidanceBytes = 32 * 1024
	// maxBundleBytes caps the compressed log bundle that is attached.
	maxBundleBytes = 8 * 1024 * 1024
)

// LLMConfig carries the OpenAI-compatible endpoint the agent should call. The
// server supplies these values in the troubleshoot command so the device never
// needs its own model configuration.
type LLMConfig struct {
	BaseURL string // e.g. "http://127.0.0.1:11434" (Ollama) or an OpenAI-compatible host
	Model   string // e.g. "llama3" or "gpt-4o-mini"
	APIKey  string // may be empty for local models
}

// Request is a single troubleshooting job, decoded from the server's
// "troubleshoot" WebSocket message.
type Request struct {
	CommandID int
	TicketID  int
	Endpoint  string // optional operator-supplied label for the endpoint
	Prompt    string // operator-supplied description of the problem
	LLM       LLMConfig
}

// Result is the finished agent output, ready to be reported to the server. A
// non-nil error alongside a Result means the job partially failed; the caller
// should still report whatever was produced.
type Result struct {
	Guidance  string // LLM remediation guidance (redacted); may be empty
	LogBundle []byte // gzip-compressed redacted log bundle; may be empty
	Endpoint  string // resolved endpoint label used in the ticket note
	Truncated bool   // true if the prompt's log sample was truncated
}

// Agent runs troubleshooting jobs. Its function fields are overridable so
// tests can stub the expensive platform and network calls.
type Agent struct {
	// CollectLogs returns raw (uncompressed) log text plus a list of the
	// human-readable sources it gathered, looking back window.
	CollectLogs func(ctx context.Context, window time.Duration) (string, []string, error)
	// CallLLM asks the model for guidance given a system and a user prompt.
	CallLLM func(ctx context.Context, cfg LLMConfig, system, user string) (string, error)
	// OnProgress, when set, is called as the job moves between stages so the
	// caller can report progress to the server. Stage is one of the Stage*
	// constants; message is a short human-readable detail.
	OnProgress func(stage, message string)

	Window time.Duration
}

// Progress stages reported through Agent.OnProgress. They match the stages
// the server accepts on POST /api/tickets/{id}/troubleshoot-status.
const (
	StageCollectingLogs = "collecting_logs"
	StageLogsCollected  = "logs_collected"
	StageAnalysing      = "analysing"
)

func (a *Agent) progress(stage, message string) {
	if a.OnProgress != nil {
		a.OnProgress(stage, message)
	}
}

// NewAgent returns an Agent wired with the real platform log collector and the
// OpenAI-compatible LLM client.
func NewAgent() *Agent {
	return &Agent{
		CollectLogs: collectLogs,
		CallLLM:     newLLMClient().Chat,
		Window:      DefaultWindow,
	}
}

// Run executes a full troubleshooting job. The returned Result is always safe
// to inspect; a non-nil error describes one or more partial failures (for
// example the LLM call failed but the log bundle was still collected) so the
// caller can decide whether to report a degraded result.
func (a *Agent) Run(ctx context.Context, req Request) (Result, error) {
	window := a.Window
	if window <= 0 {
		window = DefaultWindow
	}

	var result Result
	result.Endpoint = a.resolveEndpoint(req)

	var errs []string

	a.progress(StageCollectingLogs, fmt.Sprintf("Collecting endpoint logs from the last %s.", window))
	rawLogs, sources, collectErr := a.CollectLogs(ctx, window)
	var redacted string
	if collectErr != nil {
		errs = append(errs, fmt.Sprintf("log collection: %v", collectErr))
	} else if strings.TrimSpace(rawLogs) != "" {
		redacted = Scrub(rawLogs)
	}
	switch {
	case collectErr != nil:
		a.progress(StageLogsCollected, fmt.Sprintf("Log collection failed: %v", collectErr))
	case redacted == "":
		a.progress(StageLogsCollected, "No log entries were found in the collection window.")
	default:
		a.progress(StageLogsCollected, fmt.Sprintf("Collected %d bytes of logs from %d source(s): %s.",
			len(redacted), len(sources), strings.Join(sources, ", ")))
	}

	sample := redacted
	if len(sample) > maxLogSampleBytes {
		sample, result.Truncated = Truncate(sample, maxLogSampleBytes)
	}

	// Only attach a bundle when we actually captured logs; a bundle of just a
	// "[no logs]" placeholder would be noise on the ticket.
	if redacted != "" {
		bundle := gzipText(redacted)
		if len(bundle) > maxBundleBytes {
			bundle = bundle[:maxBundleBytes]
		}
		result.LogBundle = bundle
	}

	if req.LLM.Model != "" {
		if a.CallLLM == nil {
			errs = append(errs, "no LLM client configured")
		} else {
			userPrompt := buildUserPrompt(req, sources, sample)
			a.progress(StageAnalysing, fmt.Sprintf("Asking model %q at %s for guidance.", req.LLM.Model, req.LLM.BaseURL))
			guidance, llmErr := a.CallLLM(ctx, req.LLM, SystemPrompt, userPrompt)
			if llmErr != nil {
				errs = append(errs, fmt.Sprintf("llm: %v", llmErr))
			} else {
				result.Guidance, _ = Truncate(Scrub(guidance), maxGuidanceBytes)
			}
		}
	}

	if len(errs) > 0 {
		return result, errors.New(strings.Join(errs, "; "))
	}
	return result, nil
}

// resolveEndpoint picks the label used in the ticket note: the operator-supplied
// endpoint first, then the local hostname.
func (a *Agent) resolveEndpoint(req Request) string {
	if e := strings.TrimSpace(req.Endpoint); e != "" {
		return e
	}
	if h, err := os.Hostname(); err == nil && h != "" {
		return h
	}
	return "this device"
}

// gzipText compresses s and returns the gzip bytes, or nil on failure.
func gzipText(s string) []byte {
	var buf bytes.Buffer
	zw := gzip.NewWriter(&buf)
	if _, err := zw.Write([]byte(s)); err != nil {
		return nil
	}
	if err := zw.Close(); err != nil {
		return nil
	}
	return buf.Bytes()
}
