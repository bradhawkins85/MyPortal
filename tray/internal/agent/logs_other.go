//go:build !windows && !darwin

package agent

import (
	"context"
	"time"
)

// collectPlatformLogs is a no-op on platforms without a wired-up log source.
// The agent still runs (and can call the LLM on the operator's prompt alone);
// the server note will simply have no log bundle.
func collectPlatformLogs(context.Context, time.Duration) (string, []string, error) {
	return "", nil, ErrLogsUnsupported
}
