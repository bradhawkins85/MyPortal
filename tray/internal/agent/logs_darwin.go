//go:build darwin

package agent

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// defaultPlatformSources reads the whole unified log when the server names
// no sources.
var defaultPlatformSources = []string{"macos:all"}

// Log files may be read from these folders.
var (
	platformFileRoots = MacOSLogFileRoots
	platformPathStyle = posixPaths
)

// isPlatformLogSource reports whether source is an allowlisted macOS source
// or log file.
func isPlatformLogSource(source string) bool {
	return isMacOSLogSource(source) || isFileLogSource(source)
}

// collectPlatformLogs reads the macOS unified log for each requested source
// using `log show`, which is read-only and needs no special entitlement. Each
// source maps to a fixed predicate; unlisted sources are refused.
func collectPlatformLogs(ctx context.Context, specs []LogSpec) (string, []string, error) {
	var b strings.Builder
	var sources []string
	var lastErr error
	for _, spec := range specs {
		predicate, ok := MacOSLogPredicates[spec.Source]
		if !ok {
			b.WriteString(fmt.Sprintf("### %s Log\n[refused: not an allowlisted source]\n\n", spec.Source))
			continue
		}
		args := []string{"show", "--style", "compact", "--last", logWindow(spec.Window)}
		if predicate != "" {
			args = append(args, "--predicate", predicate)
		}
		out, err := runDarwinLog(ctx, args...)
		if err != nil {
			lastErr = err
			b.WriteString(fmt.Sprintf("### %s Log\n[unavailable: %s]\n\n", spec.Source, err))
			continue
		}
		text := strings.TrimSpace(string(out))
		if text == "" {
			text = "[no entries in this window]"
		}
		b.WriteString(fmt.Sprintf("### %s Log\n%s\n\n", spec.Source, text))
		sources = append(sources, spec.Source)
	}
	if len(sources) == 0 && lastErr != nil {
		return "", nil, fmt.Errorf("log show: %w", lastErr)
	}
	return b.String(), sources, nil
}

func runDarwinLog(ctx context.Context, args ...string) ([]byte, error) {
	bin, err := exec.LookPath("log")
	if err != nil {
		return nil, fmt.Errorf("locate log: %w", err)
	}
	var stdout, stderr bytes.Buffer
	cmd := exec.CommandContext(ctx, bin, args...)
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("timed out: %w", ctx.Err())
		}
		return nil, fmt.Errorf("%w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return stdout.Bytes(), nil
}

// logWindow formats a duration as a string the `log` tool understands
// (e.g. "24h", "30m", "45s").
func logWindow(window time.Duration) string {
	switch {
	case window.Hours() >= 1:
		return fmt.Sprintf("%dh", int(window.Hours()))
	case window.Minutes() >= 1:
		return fmt.Sprintf("%dm", int(window.Minutes()))
	default:
		return fmt.Sprintf("%ds", int(window.Seconds()))
	}
}
