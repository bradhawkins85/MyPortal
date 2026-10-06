package agent

import (
	"context"
	"errors"
	"time"
)

// ErrLogsUnsupported indicates the current platform has no log source wired up.
var ErrLogsUnsupported = errors.New("endpoint log collection is not supported on this platform")

// collectLogs gathers raw endpoint log text from the platform, looking back
// window. It returns the combined text and a list of the human-readable
// sources it read. It is strictly read-only.
func collectLogs(ctx context.Context, window time.Duration) (string, []string, error) {
	return collectPlatformLogs(ctx, window)
}
