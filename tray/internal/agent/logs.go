package agent

import (
	"context"
	"errors"
	"time"
)

// ErrLogsUnsupported indicates the current platform has no log source wired up.
var ErrLogsUnsupported = errors.New("endpoint log collection is not supported on this platform")

// collectLogs gathers raw endpoint log text from the platform for each spec.
// It returns the combined text and a list of the human-readable sources it
// read. It is strictly read-only.
func collectLogs(ctx context.Context, specs []LogSpec) (string, []string, error) {
	return collectPlatformLogs(ctx, specs)
}

// defaultLogSpecs is what a job reads when the server named no sources (the
// original single-stage troubleshooter).
func defaultLogSpecs(window time.Duration) []LogSpec {
	specs := make([]LogSpec, 0, len(defaultPlatformSources))
	for _, s := range defaultPlatformSources {
		specs = append(specs, LogSpec{Source: s, Window: window})
	}
	return specs
}
