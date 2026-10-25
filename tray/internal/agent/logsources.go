package agent

import (
	"regexp"
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

// WindowsLogSources are the Windows Event Log channels the server describes
// to the LLM. Any other well-formed channel name may also be read (see
// ValidEventLogChannel); only log files, requested separately with
// FileSourcePrefix (see logfiles.go), are limited to allowlisted folders.
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
	"Microsoft-Windows-Kernel-Power/Thermal-Operational",
	"Microsoft-Windows-Kernel-Boot/Operational",
	"Microsoft-Windows-Kernel-WHEA/Errors",
	"Microsoft-Windows-Kernel-WHEA/Operational",
	"Microsoft-Windows-Ntfs/Operational",
	"Microsoft-Windows-Storage-Storport/Operational",
	"Microsoft-Windows-Storage-Storport/Health",
	"Microsoft-Windows-StorageSpaces-Driver/Operational",
	"Microsoft-Windows-Resource-Exhaustion-Detector/Operational",
	"Microsoft-Windows-Fault-Tolerant-Heap/Operational",
}

// maxExtraEventLogs caps how many extra channels the server may add.
const maxExtraEventLogs = 50

// extraEventLogRe is the shape of an owner-approved Windows event log
// channel: a classic log ("Application") or "Provider/Type". It cannot start
// with "/" so wevtutil never reads it as an option.
// Keep in sync with _CHANNEL_RE in app/services/ai_required_logs.py.
var extraEventLogRe = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9 ._-]{0,150}(/[A-Za-z0-9][A-Za-z0-9 ._-]{0,60})?$`)

// ValidEventLogChannel reports whether name is a well-formed Windows event
// log channel name that wevtutil can be asked to read.
func ValidEventLogChannel(name string) bool {
	return name == strings.TrimSpace(name) && extraEventLogRe.MatchString(name) && !strings.Contains(name, "  ")
}

// ExtraEventLogs validates the event log channels the server's owner allowed
// on top of WindowsLogSources (TROUBLESHOOT_EVENT_LOGS_WINDOWS and channels
// approved on the required logs page). It returns the channels to allow and
// the entries that were ignored.
func ExtraEventLogs(extra []string) ([]string, []string) {
	var channels, ignored []string
	seen := map[string]bool{}
	for _, raw := range extra {
		name := strings.TrimSpace(raw)
		if name == "" || seen[name] || isWindowsLogSource(name) {
			continue
		}
		if len(channels) >= maxExtraEventLogs || !ValidEventLogChannel(name) {
			ignored = append(ignored, name)
			continue
		}
		seen[name] = true
		channels = append(channels, name)
	}
	return channels, ignored
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
