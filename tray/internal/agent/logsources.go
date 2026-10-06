package agent

import (
	"strings"
	"time"
)

// ModeCollectLogs is the troubleshoot mode used by the server's multi-stage
// troubleshooter. In this mode the agent only collects the specific logs the
// server asked for and uploads them; the server runs the LLM analysis itself,
// so no LLM call is made from the endpoint.
const ModeCollectLogs = "collect_logs"

// Bounds on how far back a single requested source may look.
const (
	MinRequestWindow = time.Hour
	MaxRequestWindow = 72 * time.Hour
)

// maxLogRequests caps how many sources one job may collect.
const maxLogRequests = 8

// LogRequest is one log source the server asked the agent to collect, with
// the reason the troubleshooter wants it (shown in progress notes).
type LogRequest struct {
	Source string `json:"source"`
	Reason string `json:"reason"`
	Hours  int    `json:"hours"`
}

// LogSpec is a validated source plus how far back to read it.
type LogSpec struct {
	Source string
	Window time.Duration
}

// WindowsLogSources is the allowlist of Windows Event Log channels the agent
// may read. The server offers the LLM exactly these names; anything else is
// refused here, so a request can never name an arbitrary channel. Names are
// embedded in a single-quoted PowerShell string, so none may contain a quote.
// Keep in sync with WINDOWS_LOG_SOURCES in app/services/ai_troubleshooter.py.
var WindowsLogSources = []string{
	"System",
	"Application",
	"Security",
	"Setup",
	"Microsoft-Windows-WLAN-AutoConfig/Operational",
	"Microsoft-Windows-NetworkProfile/Operational",
	"Microsoft-Windows-DNS-Client/Operational",
	"Microsoft-Windows-SMBClient/Connectivity",
	"Microsoft-Windows-PrintService/Admin",
	"Microsoft-Windows-PrintService/Operational",
	"Microsoft-Windows-WindowsUpdateClient/Operational",
	"Microsoft-Windows-Windows Defender/Operational",
	"Microsoft-Windows-TerminalServices-LocalSessionManager/Operational",
	"Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational",
	"Microsoft-Windows-GroupPolicy/Operational",
	"Microsoft-Windows-Bits-Client/Operational",
	"Microsoft-Windows-Diagnostics-Performance/Operational",
	"Microsoft-Windows-AAD/Operational",
	"Microsoft-Windows-User Device Registration/Admin",
	"Microsoft-Windows-Kernel-PnP/Configuration",
	"Microsoft-Windows-Time-Service/Operational",
}

// MacOSLogPredicates maps the allowlisted macOS sources to fixed `log show`
// predicates. An empty predicate reads the whole unified log.
// Keep in sync with MACOS_LOG_SOURCES in app/services/ai_troubleshooter.py.
var MacOSLogPredicates = map[string]string{
	"macos:all":         "",
	"macos:wifi":        `subsystem == "com.apple.wifi"`,
	"macos:network":     `subsystem == "com.apple.network"`,
	"macos:bluetooth":   `subsystem == "com.apple.bluetooth"`,
	"macos:printing":    `process == "cupsd"`,
	"macos:power":       `process == "powerd"`,
	"macos:kernel":      `process == "kernel"`,
	"macos:install":     `process == "installd" OR process == "softwareupdated"`,
	"macos:loginwindow": `process == "loginwindow" OR subsystem == "com.apple.Authorization"`,
}

// ResolveLogRequests validates requests against the platform allowlist and
// returns the specs to collect plus the sources that were refused. Duplicate
// sources keep the longest window.
func ResolveLogRequests(requests []LogRequest, allowed func(string) bool) ([]LogSpec, []string) {
	var specs []LogSpec
	var refused []string
	index := map[string]int{}
	for _, r := range requests {
		source := strings.TrimSpace(r.Source)
		if source == "" {
			continue
		}
		if !allowed(source) {
			refused = append(refused, source)
			continue
		}
		window := time.Duration(r.Hours) * time.Hour
		if window < MinRequestWindow {
			window = DefaultWindow
		}
		if window > MaxRequestWindow {
			window = MaxRequestWindow
		}
		if i, ok := index[source]; ok {
			if window > specs[i].Window {
				specs[i].Window = window
			}
			continue
		}
		if len(specs) >= maxLogRequests {
			refused = append(refused, source)
			continue
		}
		index[source] = len(specs)
		specs = append(specs, LogSpec{Source: source, Window: window})
	}
	return specs, refused
}

func isWindowsLogSource(source string) bool {
	for _, s := range WindowsLogSources {
		if s == source {
			return true
		}
	}
	return false
}

func isMacOSLogSource(source string) bool {
	_, ok := MacOSLogPredicates[source]
	return ok
}
