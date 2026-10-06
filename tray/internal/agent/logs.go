package agent

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
)

// ErrLogsUnsupported indicates the current platform has no log source wired up.
var ErrLogsUnsupported = errors.New("endpoint log collection is not supported on this platform")

// collectLogs gathers raw endpoint log text from the platform for each spec:
// event logs from the platform's log tool and "file:" specs from allowlisted
// log folders. It returns the combined text and a list of the human-readable
// sources it read. It is strictly read-only.
func collectLogs(ctx context.Context, specs []LogSpec, fileRoots []string) (string, []string, error) {
	var eventSpecs, fileSpecs []LogSpec
	for _, spec := range specs {
		if strings.HasPrefix(spec.Source, FileSourcePrefix) {
			fileSpecs = append(fileSpecs, spec)
		} else {
			eventSpecs = append(eventSpecs, spec)
		}
	}
	if len(fileSpecs) == 0 {
		return collectPlatformLogs(ctx, eventSpecs)
	}
	var b strings.Builder
	var sources []string
	if len(eventSpecs) > 0 {
		text, read, err := collectPlatformLogs(ctx, eventSpecs)
		if err != nil {
			// Keep the file logs; note why each event source is missing.
			for _, spec := range eventSpecs {
				fmt.Fprintf(&b, "### %s Log\n[unavailable: %s]\n\n", spec.Source, err)
			}
		} else {
			b.WriteString(text)
			sources = append(sources, read...)
		}
	}
	text, read := collectFileLogs(ctx, fileSpecs, fileRoots, platformPathStyle, time.Now())
	b.WriteString(text)
	sources = append(sources, read...)
	return b.String(), sources, nil
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
