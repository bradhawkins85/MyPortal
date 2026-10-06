//go:build !windows && !darwin

package agent

import "context"

// defaultPlatformSources is empty: this platform has no log source wired up.
var defaultPlatformSources []string

// isPlatformLogSource reports whether source may be collected here.
func isPlatformLogSource(string) bool { return false }

// collectPlatformLogs is a no-op on platforms without a wired-up log source.
// The agent still runs (and can call the LLM on the operator's prompt alone);
// the server note will simply have no log bundle.
func collectPlatformLogs(context.Context, []LogSpec) (string, []string, error) {
	return "", nil, ErrLogsUnsupported
}
