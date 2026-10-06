// Command tray-troubleshoot runs the AI troubleshooting agent manually, without
// the tray service or WebSocket. It is a debugging tool for the log collector
// and the LLM path on a given host.
//
// Usage:
//
//	tray-troubleshoot -prompt "WiFi keeps dropping every few minutes" \
//	    -llm-base-url http://127.0.0.1:11434 -llm-model llama3 \
//	    [-llm-api-key KEY] [-endpoint LABEL] [-window 24h] \
//	    [-out bundle.gz] [-no-llm]
//
// The LLM guidance is printed to stdout; with -out the compressed, redacted log
// bundle is written to the given path. When no model or base URL is supplied
// (or -no-llm is set) the LLM call is skipped and only the log bundle is
// produced.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/agent"
)

func main() {
	var (
		prompt    = flag.String("prompt", "Investigate recent errors on this endpoint.", "Description of the problem to troubleshoot.")
		endpoint  = flag.String("endpoint", "", "Optional endpoint label (defaults to the hostname).")
		baseURL   = flag.String("llm-base-url", "", "OpenAI-compatible LLM base URL, e.g. http://127.0.0.1:11434. Empty skips the LLM call.")
		model     = flag.String("llm-model", "", "LLM model name, e.g. llama3. Empty skips the LLM call.")
		apiKey    = flag.String("llm-api-key", "", "Optional LLM API key.")
		windowStr = flag.String("window", "24h", "How far back to collect logs, e.g. 6h, 24h, 72h.")
		outPath   = flag.String("out", "", "Optional path to write the gzip log bundle to.")
		noLLM     = flag.Bool("no-llm", false, "Skip the LLM call and only collect + redact logs.")
	)
	flag.Parse()

	window, err := time.ParseDuration(*windowStr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "invalid -window %q: %v\n", *windowStr, err)
		flag.Usage()
		os.Exit(2)
	}

	cfg := agent.LLMConfig{BaseURL: *baseURL, Model: *model, APIKey: *apiKey}
	if *noLLM || strings.TrimSpace(*model) == "" || strings.TrimSpace(*baseURL) == "" {
		cfg.Model = "" // empty model => the agent skips the LLM call
	}

	req := agent.Request{
		Endpoint: *endpoint,
		Prompt:   *prompt,
		LLM:      cfg,
	}

	ag := agent.NewAgent()
	ag.Window = window

	// Allow Ctrl-C to abort a slow LLM call.
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-stop
		cancel()
	}()

	start := time.Now()
	result, runErr := ag.Run(ctx, req)
	elapsed := time.Since(start)

	fmt.Fprintf(os.Stderr, "troubleshoot agent finished in %s\n", elapsed.Round(time.Millisecond))
	if runErr != nil {
		fmt.Fprintf(os.Stderr, "partial failure: %v\n", runErr)
	}

	fmt.Println("---- Guidance ----")
	if result.Guidance == "" {
		fmt.Println("(no guidance)")
	} else {
		fmt.Println(result.Guidance)
	}

	if len(result.LogBundle) > 0 {
		if *outPath != "" {
			if err := os.WriteFile(*outPath, result.LogBundle, 0600); err != nil {
				fmt.Fprintf(os.Stderr, "write bundle: %v\n", err)
				os.Exit(1)
			}
			fmt.Fprintf(os.Stderr, "log bundle written to %s (%d bytes)\n", *outPath, len(result.LogBundle))
		} else {
			fmt.Fprintf(os.Stderr, "log bundle: %d bytes (use -out to save)\n", len(result.LogBundle))
		}
	} else {
		fmt.Fprintln(os.Stderr, "no log bundle produced")
	}

	if runErr != nil {
		os.Exit(1)
	}
}
