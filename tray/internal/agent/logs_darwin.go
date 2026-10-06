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

// collectPlatformLogs reads the macOS unified log for the last window using
// `log show`, which is read-only and needs no special entitlement.
func collectPlatformLogs(ctx context.Context, window time.Duration) (string, []string, error) {
	out, err := runDarwinLog(ctx, "show", "--style", "compact", "--last", logWindow(window))
	if err != nil {
		return "", nil, fmt.Errorf("log show: %w", err)
	}
	text := strings.TrimSpace(string(out))
	if text == "" {
		text = "[no entries in this window]"
	}
	return text, []string{"unified log"}, nil
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
