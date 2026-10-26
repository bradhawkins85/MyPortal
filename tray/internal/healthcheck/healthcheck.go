// Package healthcheck runs the AI health check's endpoint side. When the
// MyPortal server sends a "health_check" command over the device WebSocket
// the tray service:
//
//  1. (snapshot phase) reads a snapshot of the computer's health from
//     built-in metrics: on Windows a fixed, read-only PowerShell script reads
//     CIM/WMI classes, the registry and event logs (disks and their
//     reliability counters and failure prediction, volume free space and
//     dirty bits, defrag and TRIM state, thermal zones, CPU throttling,
//     memory, boot performance, crashes, WHEA errors, drivers, updates,
//     security and battery); macOS uses system_profiler, pmset and sysctl;
//  2. on physical Windows hardware reads each drive's SMART attributes with
//     CrystalDiskInfo, which is installed with Chocolatey when it is missing
//     and stays installed afterwards;
//  3. on physical Windows hardware, when the server allows it, installs the
//     PawnIO driver for the length of the check, reads hardware sensors with
//     the bundled LibreHardwareMonitor library and removes PawnIO again
//     before reporting back (a marker file makes the next service start
//     remove it if the service stops part-way);
//  4. collects the allowlisted logs the server asked for, redacted, through
//     the troubleshooting agent (package agent).
//
// The server reviews the result with its language model; nothing here
// changes the computer apart from the temporary PawnIO driver and installing
// CrystalDiskInfo (and Chocolatey) when they are missing.
package healthcheck

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/agent"
)

// Phases of a health check command.
const (
	PhaseSnapshot = "snapshot"
	PhaseLogs     = "logs"
)

// Progress stages reported to POST /api/health-checks/{id}/device-status.
const (
	StageReceived           = "received"
	StageCollectingSnapshot = "collecting_snapshot"
	StageReadingSMART       = "reading_smart"
	StageReadingSensors     = "reading_sensors"
	StageCollectingLogs     = "collecting_logs"
	StageUploading          = "uploading"
	StageFailed             = "failed"
)

// DefaultEventDays is how far back the snapshot counts events.
const DefaultEventDays = 7

// snapshotTimeout bounds the built-in metrics script; the fragmentation
// analysis and DISM inside it have their own shorter limits.
const snapshotTimeout = 9 * time.Minute

// Request is one health check command from the server.
type Request struct {
	CommandID       int
	HealthCheckID   int
	Phase           string
	LogRequests     []agent.LogRequest
	ExtraLogFolders []string
	ExtraEventLogs  []string
	AllowPawnIO     bool
	EventDays       int
}

// Result is what the device uploads. A non-nil error from Run alongside a
// Result means part of the check failed; whatever was produced is still sent.
type Result struct {
	Snapshot  []byte // JSON (snapshot phase only)
	LogBundle []byte // .tar.gz of redacted logs, one file per source
}

// Snapshot is the JSON document sent in the snapshot phase.
type Snapshot struct {
	Schema           int               `json:"schema"`
	Platform         string            `json:"platform"`
	CollectedAt      string            `json:"collected_at"`
	DurationMS       int64             `json:"duration_ms"`
	AgentVersion     string            `json:"agent_version,omitempty"`
	IsVirtual        *bool             `json:"is_virtual,omitempty"`
	HardwareIdentity map[string]string `json:"hardware_identity,omitempty"`
	Sections         json.RawMessage   `json:"sections"`
	Errors           map[string]string `json:"errors"`
	DiskSMART        *DiskSMARTReport  `json:"disk_smart,omitempty"`
	Sensors          *SensorReport     `json:"sensors,omitempty"`
	PawnIO           *PawnIOReport     `json:"pawnio,omitempty"`
}

// SensorReport holds hardware sensor readings (LibreHardwareMonitor).
type SensorReport struct {
	Status   string          `json:"status"` // available, unavailable, error
	Source   string          `json:"source,omitempty"`
	Detail   string          `json:"detail,omitempty"`
	Readings []SensorReading `json:"readings"`
}

// SensorReading is one sensor value.
type SensorReading struct {
	HardwareType string   `json:"hardware_type"`
	Hardware     string   `json:"hardware"`
	Type         string   `json:"type"`
	Name         string   `json:"name"`
	Value        *float64 `json:"value"`
	Min          *float64 `json:"min,omitempty"`
	Max          *float64 `json:"max,omitempty"`
	Unit         string   `json:"unit,omitempty"`
}

// PawnIOReport says what happened to the PawnIO driver during the check.
type PawnIOReport struct {
	Status string `json:"status"`
	Detail string `json:"detail,omitempty"`
}

// PawnIO statuses; the server shows a sentence for each.
const (
	PawnIOInstalledAndRemoved = "installed_and_removed"
	PawnIOAlreadyInstalled    = "already_installed"
	PawnIOSkippedVirtual      = "skipped_virtual"
	PawnIOSkippedDisabled     = "skipped_disabled"
	PawnIOSkippedUnsupported  = "skipped_unsupported"
	PawnIOBundleMissing       = "bundle_missing"
	PawnIOInstallFailed       = "install_failed"
	PawnIORemovalFailed       = "removal_failed"
	PawnIOIdentifyFailed      = "identify_failed"
)

// platformSnapshot is the raw output of a platform collector.
type platformSnapshot struct {
	Schema    int               `json:"schema"`
	Sections  json.RawMessage   `json:"sections"`
	Errors    map[string]string `json:"errors"`
	ElapsedMS int64             `json:"elapsed_ms"`
}

// Runner runs health check commands. Its function fields can be replaced in
// tests so no platform tool or driver is touched.
type Runner struct {
	// StateDir is the tray service's state folder (PawnIO marker, the
	// temporary sensor bundle).
	StateDir     string
	AgentVersion string
	// OnProgress, when set, reports a stage and a short message.
	OnProgress func(stage, message string)

	collectSnapshot func(ctx context.Context, eventDays int) (platformSnapshot, error)
	identify        func() (string, string, error)
	diskSMART       func(ctx context.Context) DiskSMARTReport
	sensors         func(ctx context.Context, allowPawnIO bool, identity machineIdentity) (SensorReport, PawnIOReport)
	newAgent        func() *agent.Agent
	now             func() time.Time
	platform        string
}

// machineIdentity is the hardware maker and model, used to tell physical
// hardware from virtual machines.
type machineIdentity struct {
	Manufacturer string
	Model        string
	Err          error
}

// NewRunner returns a Runner wired to this platform's collectors.
func NewRunner(stateDir, agentVersion string) *Runner {
	r := &Runner{
		StateDir:        stateDir,
		AgentVersion:    agentVersion,
		collectSnapshot: collectPlatformSnapshot,
		identify:        platformIdentity,
		diskSMART:       platformDiskSMART,
		newAgent:        agent.NewAgent,
		now:             time.Now,
		platform:        platformName,
	}
	r.sensors = func(ctx context.Context, allow bool, identity machineIdentity) (SensorReport, PawnIOReport) {
		return platformSensors(ctx, r.StateDir, allow, identity)
	}
	return r
}

func (r *Runner) progress(stage, message string) {
	if r.OnProgress != nil {
		r.OnProgress(stage, message)
	}
}

// Run executes one health check command.
func (r *Runner) Run(ctx context.Context, req Request) (Result, error) {
	var result Result
	var errs []string
	switch req.Phase {
	case PhaseSnapshot, PhaseLogs:
	default:
		return result, fmt.Errorf("unknown health check phase %q", req.Phase)
	}
	if req.Phase == PhaseSnapshot {
		snapshot, err := r.snapshot(ctx, req)
		if err != nil {
			errs = append(errs, err.Error())
		}
		data, marshalErr := json.Marshal(snapshot)
		if marshalErr != nil {
			errs = append(errs, fmt.Sprintf("encoding the snapshot: %v", marshalErr))
		} else {
			result.Snapshot = data
		}
	}
	if len(req.LogRequests) > 0 && ctx.Err() == nil {
		bundle, err := r.logs(ctx, req)
		result.LogBundle = bundle
		if err != nil {
			errs = append(errs, err.Error())
		}
	}
	if len(errs) > 0 {
		return result, errors.New(strings.Join(errs, "; "))
	}
	return result, nil
}

func (r *Runner) snapshot(ctx context.Context, req Request) (Snapshot, error) {
	start := r.now()
	snapshot := Snapshot{
		Schema:       1,
		Platform:     r.platform,
		CollectedAt:  start.UTC().Format(time.RFC3339),
		AgentVersion: r.AgentVersion,
		Sections:     json.RawMessage("{}"),
		Errors:       map[string]string{},
	}
	var errs []string
	identity := machineIdentity{}
	identity.Manufacturer, identity.Model, identity.Err = r.identify()
	if identity.Err == nil && strings.TrimSpace(identity.Manufacturer+identity.Model) != "" {
		virtual := isVirtualMachine(identity.Manufacturer, identity.Model)
		snapshot.IsVirtual = &virtual
		snapshot.HardwareIdentity = map[string]string{
			"manufacturer": strings.TrimSpace(identity.Manufacturer),
			"model":        strings.TrimSpace(identity.Model),
		}
	}

	days := req.EventDays
	if days < 1 || days > 90 {
		days = DefaultEventDays
	}
	r.progress(StageCollectingSnapshot, "Reading built-in health metrics (disks, chkdsk and defrag state, temperatures, crashes, performance, updates and security).")
	scriptCtx, cancel := context.WithTimeout(ctx, snapshotTimeout)
	raw, err := r.collectSnapshot(scriptCtx, days)
	cancel()
	if err != nil {
		errs = append(errs, fmt.Sprintf("health metrics: %v", err))
		snapshot.Errors["snapshot"] = truncate(err.Error(), 500)
	}
	if len(raw.Sections) > 0 && json.Valid(raw.Sections) {
		snapshot.Sections = raw.Sections
	}
	for name, message := range raw.Errors {
		snapshot.Errors[name] = truncate(message, 500)
	}

	if ctx.Err() == nil && r.platform == "windows" {
		if snapshot.IsVirtual != nil && *snapshot.IsVirtual {
			snapshot.DiskSMART = &DiskSMARTReport{Status: SMARTSkippedVirtual, Source: smartSource, Disks: []SMARTDisk{}}
		} else {
			r.progress(StageReadingSMART, "Reading drive SMART data with CrystalDiskInfo (installed with Chocolatey when missing; it stays installed).")
			report := r.diskSMART(ctx)
			snapshot.DiskSMART = &report
			if report.Status == SMARTError {
				snapshot.Errors["disk_smart"] = truncate(report.Detail, 500)
			}
		}
	}

	if ctx.Err() == nil {
		r.progress(StageReadingSensors, sensorProgressMessage(r.platform, req.AllowPawnIO, snapshot.IsVirtual))
		sensors, pawn := r.sensors(ctx, req.AllowPawnIO, identity)
		snapshot.Sensors = &sensors
		snapshot.PawnIO = &pawn
		if pawn.Status == PawnIORemovalFailed {
			errs = append(errs, "PawnIO could not be removed: "+pawn.Detail)
		}
	}
	snapshot.DurationMS = r.now().Sub(start).Milliseconds()
	if len(errs) > 0 {
		return snapshot, errors.New(strings.Join(errs, "; "))
	}
	return snapshot, nil
}

func sensorProgressMessage(platform string, allow bool, virtual *bool) string {
	switch {
	case platform != "windows":
		return "Reading hardware sensors."
	case !allow:
		return "Reading hardware sensors without PawnIO (turned off for this check)."
	case virtual != nil && *virtual:
		return "Reading hardware sensors; PawnIO is not installed on virtual machines."
	default:
		return "Installing PawnIO for the length of the check to read hardware sensors; it is removed straight afterwards."
	}
}

func (r *Runner) logs(ctx context.Context, req Request) ([]byte, error) {
	ag := r.newAgent()
	ag.OnProgress = func(stage, message string) {
		if stage == agent.StageCollectingLogs {
			r.progress(StageCollectingLogs, message)
		}
	}
	out, err := ag.Run(ctx, agent.Request{
		CommandID:       req.CommandID,
		Mode:            agent.ModeCollectLogs,
		LogRequests:     req.LogRequests,
		ExtraLogFolders: req.ExtraLogFolders,
		ExtraEventLogs:  req.ExtraEventLogs,
	})
	if err != nil {
		return out.LogBundle, fmt.Errorf("logs: %w", err)
	}
	return out.LogBundle, nil
}

func truncate(value string, limit int) string {
	r := []rune(value)
	if len(r) > limit {
		return string(r[:limit])
	}
	return value
}
