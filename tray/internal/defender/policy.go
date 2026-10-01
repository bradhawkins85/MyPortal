package defender

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// Policy item statuses reported to the portal.
const (
	StatusApplied         = "applied"
	StatusRemoved         = "removed"
	StatusBlockedByTamper = "blocked_tamper_protection"
	StatusManagedByPolicy = "managed_by_policy"
	StatusUnsupported     = "unsupported"
	StatusFailed          = "failed"
	statusPending         = "pending"
)

// maxMessageLength keeps PowerShell errors readable in the portal.
const maxMessageLength = 1000

// exclusionKinds maps portal exclusion types to Get-MpPreference properties.
var exclusionKinds = []string{"path", "process", "extension"}

// Tamper Protection guards exclusions whenever the device reports the
// TPExclusions feature, and older platforms do not report it at all. An
// exclusion change is therefore only attempted when Tamper Protection is off
// or Defender explicitly says exclusions are not protected. Blocked attempts
// raise Tamper Protection alerts, so a change is never tried just to find out.
const tamperExclusionsMessage = "Tamper Protection is on and may block exclusion changes, so the agent did not attempt this change. " +
	"Deploy the exclusion through Intune or Microsoft Defender for Endpoint, or turn off Tamper Protection on this device."

// environment is the probe's view of the endpoint's Defender configuration.
type environment struct {
	TamperProtected           bool     `json:"tamper_protected"`
	TamperProtectionSource    string   `json:"tamper_protection_source"`
	TamperProtectedExclusions *bool    `json:"tamper_protected_exclusions"`
	LocalAdminMergeDisabled   bool     `json:"local_admin_merge_disabled"`
	ScheduleManagedByPolicy   bool     `json:"schedule_managed_by_policy"`
	ExclusionsReadable        bool     `json:"exclusions_readable"`
	ExclusionPath             []string `json:"exclusion_path"`
	ExclusionProcess          []string `json:"exclusion_process"`
	ExclusionExtension        []string `json:"exclusion_extension"`
	ScanParameters            int      `json:"scan_parameters"`
	ScanScheduleDay           int      `json:"scan_schedule_day"`
	ScanScheduleMinutes       int      `json:"scan_schedule_minutes"`
}

func (e environment) exclusions(kind string) []string {
	switch kind {
	case "path":
		return e.ExclusionPath
	case "process":
		return e.ExclusionProcess
	case "extension":
		return e.ExclusionExtension
	}
	return nil
}

func (e environment) schedule() scheduleSetting {
	return scheduleSetting{Parameters: e.ScanParameters, Day: e.ScanScheduleDay, Minutes: e.ScanScheduleMinutes}
}

// exclusionsLockedByTamper reports whether Tamper Protection may block
// exclusion changes on this endpoint.
func (e environment) exclusionsLockedByTamper() bool {
	return e.TamperProtected && (e.TamperProtectedExclusions == nil || *e.TamperProtectedExclusions)
}

// fingerprint identifies the local controls that decide whether a change can
// succeed. A blocked change is retried only after this changes.
func (e environment) fingerprint() string {
	tpx := "unknown"
	if e.TamperProtectedExclusions != nil {
		tpx = strconv.FormatBool(*e.TamperProtectedExclusions)
	}
	return fmt.Sprintf("tp=%t;tpx=%s;merge=%t;schedule=%t", e.TamperProtected, tpx, e.LocalAdminMergeDisabled, e.ScheduleManagedByPolicy)
}

// scheduleSetting uses Defender's own values: ScanParameters 1=quick 2=full,
// ScanScheduleDay 0=every day, 1=Sunday ... 7=Saturday, 8=never.
type scheduleSetting struct {
	Parameters int `json:"parameters"`
	Day        int `json:"day"`
	Minutes    int `json:"minutes"`
}

type operations struct {
	Add      map[string][]string `json:"add"`
	Remove   map[string][]string `json:"remove"`
	Schedule *scheduleSetting    `json:"schedule,omitempty"`
}

func (o operations) empty() bool {
	for _, kind := range exclusionKinds {
		if len(o.Add[kind]) > 0 || len(o.Remove[kind]) > 0 {
			return false
		}
	}
	return o.Schedule == nil
}

type blockedChange struct {
	Status      string `json:"status"`
	Message     string `json:"message"`
	Fingerprint string `json:"fingerprint"`
}

// policyState is persisted between runs so that the agent only ever removes
// exclusions it added itself and can restore the schedule it replaced.
type policyState struct {
	Exclusions       map[string][]string      `json:"exclusions"`
	ScheduleManaged  bool                     `json:"schedule_managed"`
	OriginalSchedule *scheduleSetting         `json:"original_schedule,omitempty"`
	Blocked          map[string]blockedChange `json:"blocked,omitempty"`
}

type plannedItem struct {
	item api.DefenderPolicyItem
	key  string
	// kind and value identify exclusion changes; schedule identifies a
	// schedule change and restore marks a return to the original schedule.
	kind     string
	value    string
	schedule *scheduleSetting
	restore  bool
}

var (
	probeFunc = probe
	applyFunc = applyOperations
	nowFunc   = time.Now
)

// ApplyPolicy reconciles the portal's Defender policy with the endpoint and
// reports the outcome of every setting. Changes that Tamper Protection could
// block are skipped rather than attempted.
func ApplyPolicy(ctx context.Context, policy api.DefenderPolicy, statePath string) (*api.DefenderPolicyResult, error) {
	env, err := probeFunc(ctx)
	if err != nil {
		return nil, fmt.Errorf("read Microsoft Defender preferences: %w", err)
	}
	state := loadPolicyState(statePath)
	ops, items := plan(policy, env, &state)
	if !ops.empty() {
		applyErrors, applyErr := applyFunc(ctx, ops)
		verified, probeErr := probeFunc(ctx)
		if probeErr != nil {
			verified = env
			if applyErr == nil {
				applyErr = fmt.Errorf("verify Microsoft Defender preferences: %w", probeErr)
			}
		}
		finalize(items, verified, env, &state, applyErrors, applyErr)
	}
	if err := savePolicyState(statePath, state); err != nil {
		return nil, err
	}
	return buildResult(env, items), nil
}

func plan(policy api.DefenderPolicy, env environment, state *policyState) (operations, []*plannedItem) {
	ops := operations{Add: map[string][]string{}, Remove: map[string][]string{}}
	var items []*plannedItem
	previouslyBlocked := state.Blocked
	state.Blocked = map[string]blockedChange{}
	if state.Exclusions == nil {
		state.Exclusions = map[string][]string{}
	}
	fingerprint := env.fingerprint()
	// recall returns true when an identical change already failed under the
	// same local controls, so it is reported again without being retried.
	recall := func(p *plannedItem) bool {
		blocked, ok := previouslyBlocked[p.key]
		if !ok || blocked.Fingerprint != fingerprint {
			return false
		}
		state.Blocked[p.key] = blocked
		p.item.Status = blocked.Status
		p.item.Message = blocked.Message + " The agent will not retry until the policy or the device's protection settings change."
		return true
	}

	desired := map[string][]string{}
	for _, exclusion := range policy.Exclusions {
		kind := strings.ToLower(strings.TrimSpace(exclusion.Type))
		value := strings.TrimSpace(exclusion.Value)
		if value == "" {
			continue
		}
		if !isExclusionKind(kind) {
			items = append(items, &plannedItem{item: api.DefenderPolicyItem{
				Setting: "exclusion_" + kind, Value: value, Action: "add", Status: StatusUnsupported,
				Message: "Microsoft Defender Antivirus has no " + kind + " exclusions; this entry cannot be applied.",
			}})
			continue
		}
		if !containsFold(desired[kind], value) {
			desired[kind] = append(desired[kind], value)
		}
	}

	for _, kind := range exclusionKinds {
		current := env.exclusions(kind)
		for _, value := range desired[kind] {
			p := &plannedItem{key: "add|" + kind + "|" + strings.ToLower(value), kind: kind, value: value,
				item: api.DefenderPolicyItem{Setting: "exclusion_" + kind, Value: value, Action: "add"}}
			items = append(items, p)
			switch {
			case env.ExclusionsReadable && containsFold(current, value):
				p.item.Status = StatusApplied
			case env.exclusionsLockedByTamper():
				p.item.Status, p.item.Message = StatusBlockedByTamper, tamperExclusionsMessage
			case env.LocalAdminMergeDisabled:
				p.item.Status = StatusManagedByPolicy
				p.item.Message = "Group Policy or MDM disables local exclusions (DisableLocalAdminMerge), so Defender would ignore this exclusion."
			case !env.ExclusionsReadable:
				p.item.Status, p.item.Message = StatusFailed, "The agent cannot read the device's current Defender exclusions."
			case recall(p):
			default:
				p.item.Status = statusPending
				ops.Add[kind] = append(ops.Add[kind], value)
			}
		}
		var kept []string
		for _, value := range state.Exclusions[kind] {
			if containsFold(desired[kind], value) {
				kept = append(kept, value)
				continue
			}
			if env.ExclusionsReadable && !containsFold(current, value) {
				continue // already gone; nothing left to own
			}
			kept = append(kept, value)
			p := &plannedItem{key: "remove|" + kind + "|" + strings.ToLower(value), kind: kind, value: value,
				item: api.DefenderPolicyItem{Setting: "exclusion_" + kind, Value: value, Action: "remove"}}
			items = append(items, p)
			switch {
			case env.exclusionsLockedByTamper():
				p.item.Status, p.item.Message = StatusBlockedByTamper, tamperExclusionsMessage
			case !env.ExclusionsReadable:
				p.item.Status, p.item.Message = StatusFailed, "The agent cannot read the device's current Defender exclusions."
			case recall(p):
			default:
				p.item.Status = statusPending
				ops.Remove[kind] = append(ops.Remove[kind], value)
			}
		}
		state.Exclusions[kind] = kept
	}

	// Tamper Protection does not guard the scan schedule, but Group Policy or
	// MDM overrides any local value, so the agent leaves managed schedules be.
	if wanted, ok := desiredSchedule(policy.ScheduledScan); ok {
		p := &plannedItem{key: fmt.Sprintf("schedule|%d|%d|%d", wanted.Parameters, wanted.Day, wanted.Minutes), schedule: &wanted,
			item: api.DefenderPolicyItem{Setting: "scheduled_scan", Value: describeSchedule(wanted), Action: "set"}}
		items = append(items, p)
		switch {
		case env.ScheduleManagedByPolicy:
			p.item.Status = StatusManagedByPolicy
			p.item.Message = "Group Policy or MDM sets this device's scan schedule, which overrides the MyPortal schedule."
		case env.schedule() == wanted:
			p.item.Status = StatusApplied
			if !state.ScheduleManaged {
				state.ScheduleManaged, state.OriginalSchedule = true, &wanted
			}
		case recall(p):
		default:
			p.item.Status = statusPending
			ops.Schedule = &wanted
		}
	} else if state.ScheduleManaged {
		original := state.OriginalSchedule
		switch {
		case env.ScheduleManagedByPolicy || original == nil || env.schedule() == *original:
			state.ScheduleManaged, state.OriginalSchedule = false, nil
		default:
			p := &plannedItem{key: fmt.Sprintf("restore|%d|%d|%d", original.Parameters, original.Day, original.Minutes), schedule: original, restore: true,
				item: api.DefenderPolicyItem{Setting: "scheduled_scan", Value: describeSchedule(*original), Action: "restore"}}
			items = append(items, p)
			if !recall(p) {
				p.item.Status = statusPending
				ops.Schedule = original
			}
		}
	}
	return ops, items
}

// finalize verifies each attempted change against a fresh read of the
// endpoint. Set-MpPreference reports success even when Tamper Protection or a
// policy discards a change, so verification is the only reliable signal.
func finalize(items []*plannedItem, verified, before environment, state *policyState, applyErrors map[string]string, applyErr error) {
	for _, p := range items {
		if p.item.Status != statusPending {
			continue
		}
		var ok bool
		var errorKey string
		switch {
		case p.kind != "" && p.item.Action == "add":
			ok = verified.ExclusionsReadable && containsFold(verified.exclusions(p.kind), p.value)
			errorKey = "add|" + p.kind + "|" + p.value
		case p.kind != "":
			ok = verified.ExclusionsReadable && !containsFold(verified.exclusions(p.kind), p.value)
			errorKey = "remove|" + p.kind + "|" + p.value
		case p.schedule != nil:
			ok = verified.schedule() == *p.schedule
			errorKey = "schedule"
		}
		if ok {
			delete(state.Blocked, p.key)
			switch {
			case p.kind != "" && p.item.Action == "add":
				p.item.Status = StatusApplied
				state.Exclusions[p.kind] = appendFold(state.Exclusions[p.kind], p.value)
			case p.kind != "":
				p.item.Status = StatusRemoved
				state.Exclusions[p.kind] = removeFold(state.Exclusions[p.kind], p.value)
			case p.restore:
				p.item.Status = StatusApplied
				state.ScheduleManaged, state.OriginalSchedule = false, nil
			default:
				p.item.Status = StatusApplied
				if !state.ScheduleManaged {
					original := before.schedule()
					state.ScheduleManaged, state.OriginalSchedule = true, &original
				}
			}
			continue
		}
		message := applyErrors[errorKey]
		if message == "" && applyErr != nil {
			message = applyErr.Error()
		}
		if verified.TamperProtected {
			p.item.Status = StatusBlockedByTamper
			p.item.Message = strings.TrimSpace("Defender did not keep this change and Tamper Protection is on. " + message)
		} else {
			p.item.Status = StatusFailed
			if message == "" {
				message = "Defender did not keep this change; Group Policy, Intune or another security product may be overriding it."
			}
			p.item.Message = message
		}
		if runes := []rune(p.item.Message); len(runes) > maxMessageLength {
			p.item.Message = string(runes[:maxMessageLength])
		}
		state.Blocked[p.key] = blockedChange{Status: p.item.Status, Message: p.item.Message, Fingerprint: verified.fingerprint()}
	}
}

func buildResult(env environment, planned []*plannedItem) *api.DefenderPolicyResult {
	result := &api.DefenderPolicyResult{
		EvaluatedAt: nowFunc().UTC(),
		TamperProtection: api.DefenderTamperState{
			Enabled:                 env.TamperProtected,
			Source:                  env.TamperProtectionSource,
			ProtectsExclusions:      env.TamperProtectedExclusions,
			LocalAdminMergeDisabled: env.LocalAdminMergeDisabled,
			ScheduleManagedByPolicy: env.ScheduleManagedByPolicy,
		},
		Items: make([]api.DefenderPolicyItem, 0, len(planned)),
	}
	problems, tamper := 0, 0
	for _, p := range planned {
		result.Items = append(result.Items, p.item)
		switch p.item.Status {
		case StatusApplied, StatusRemoved:
		case StatusBlockedByTamper:
			problems++
			tamper++
		default:
			problems++
		}
	}
	switch {
	case len(planned) == 0:
		result.Status = "not_configured"
	case problems == 0:
		result.Status = "applied"
	case problems < len(planned):
		result.Status = "partial"
	case tamper > 0:
		result.Status = "blocked"
	default:
		result.Status = "failed"
	}
	return result
}

func desiredSchedule(scan *api.DefenderScheduledScan) (scheduleSetting, bool) {
	if scan == nil || scan.Day == nil || *scan.Day < 0 || *scan.Day > 6 {
		return scheduleSetting{}, false
	}
	var parameters int
	switch scan.Type {
	case "quick":
		parameters = 1
	case "full":
		parameters = 2
	default:
		return scheduleSetting{}, false
	}
	clock, err := time.Parse("15:04", strings.TrimSpace(scan.Time))
	if err != nil {
		return scheduleSetting{}, false
	}
	// The portal numbers days from Monday (0); Defender numbers them from
	// Sunday (1) and reserves 0 for "every day".
	return scheduleSetting{Parameters: parameters, Day: (*scan.Day+1)%7 + 1, Minutes: clock.Hour()*60 + clock.Minute()}, true
}

func describeSchedule(s scheduleSetting) string {
	kind := map[int]string{1: "Quick scan", 2: "Full scan"}[s.Parameters]
	if kind == "" {
		kind = "Scan"
	}
	day := map[int]string{0: "every day", 1: "Sunday", 2: "Monday", 3: "Tuesday", 4: "Wednesday", 5: "Thursday",
		6: "Friday", 7: "Saturday", 8: "never"}[s.Day]
	return fmt.Sprintf("%s %s at %02d:%02d", kind, day, s.Minutes/60, s.Minutes%60)
}

func isExclusionKind(kind string) bool {
	for _, k := range exclusionKinds {
		if k == kind {
			return true
		}
	}
	return false
}

func containsFold(values []string, value string) bool {
	for _, v := range values {
		if strings.EqualFold(strings.TrimSpace(v), value) {
			return true
		}
	}
	return false
}

func appendFold(values []string, value string) []string {
	if containsFold(values, value) {
		return values
	}
	return append(values, value)
}

func removeFold(values []string, value string) []string {
	kept := values[:0:0]
	for _, v := range values {
		if !strings.EqualFold(v, value) {
			kept = append(kept, v)
		}
	}
	return kept
}

func loadPolicyState(path string) policyState {
	var state policyState
	if data, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(data, &state)
	}
	return state
}

func savePolicyState(path string, state policyState) error {
	for kind, values := range state.Exclusions {
		sort.Strings(values)
		if len(values) == 0 {
			delete(state.Exclusions, kind)
		}
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return fmt.Errorf("save Defender policy state: %w", err)
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		return fmt.Errorf("save Defender policy state: %w", err)
	}
	return nil
}

const probeScript = `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$s = Get-MpComputerStatus
$p = Get-MpPreference
function Get-RegistryValue([string]$Path, [string]$Name) {
  try { (Get-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction Stop).$Name } catch { $null }
}
function Test-Configured([string]$Path, [string[]]$Names) {
  foreach ($name in $Names) { if ($null -ne (Get-RegistryValue $Path $name)) { return $true } }
  return $false
}
$policy = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows Defender'
$mdm = Join-Path $policy 'Policy Manager'
$tpExclusions = Get-RegistryValue 'HKLM:\SOFTWARE\Microsoft\Windows Defender\Features' 'TPExclusions'
$mergeDisabled = ((Get-RegistryValue $policy 'DisableLocalAdminMerge') -eq 1) -or ((Get-RegistryValue $mdm 'DisableLocalAdminMerge') -eq 1)
$scheduleManaged = (Test-Configured (Join-Path $policy 'Scan') @('ScheduleDay', 'ScheduleTime', 'ScanParameters')) -or (Test-Configured $mdm @('ScheduleScanDay', 'ScheduleScanTime', 'ScanParameter'))
function Get-Exclusions($Values) { @($Values | Where-Object { $_ } | ForEach-Object { ([string]$_).Trim() }) }
$readable = -not (@($p.ExclusionPath) + @($p.ExclusionProcess) + @($p.ExclusionExtension) | Where-Object { ([string]$_) -like 'N/A*' })
$time = $p.ScanScheduleTime
$minutes = if ($time -is [TimeSpan]) { [int]$time.TotalMinutes } elseif ($time -is [datetime]) { [int]$time.TimeOfDay.TotalMinutes } elseif ($null -ne $time) { [int]$time } else { 0 }
[ordered]@{
  tamper_protected = [bool]$s.IsTamperProtected
  tamper_protection_source = [string]$s.TamperProtectionSource
  tamper_protected_exclusions = if ($null -eq $tpExclusions) { $null } else { [int]$tpExclusions -eq 1 }
  local_admin_merge_disabled = [bool]$mergeDisabled
  schedule_managed_by_policy = [bool]$scheduleManaged
  exclusions_readable = [bool]$readable
  exclusion_path = @(Get-Exclusions $p.ExclusionPath)
  exclusion_process = @(Get-Exclusions $p.ExclusionProcess)
  exclusion_extension = @(Get-Exclusions $p.ExclusionExtension)
  scan_parameters = [int]$p.ScanParameters
  scan_schedule_day = [int]$p.ScanScheduleDay
  scan_schedule_minutes = $minutes
} | ConvertTo-Json -Depth 3 -Compress`

func probe(ctx context.Context) (environment, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	out, err := runPowerShell(ctx, probeScript)
	if err != nil {
		return environment{}, err
	}
	var env environment
	if err := json.Unmarshal(trimOutput(out), &env); err != nil {
		return environment{}, fmt.Errorf("decode Microsoft Defender preferences: %w", err)
	}
	return env, nil
}

// applyScript makes only the changes listed in the encoded operations. Values
// are carried as base64 JSON rather than interpolated, so an exclusion value
// can never become PowerShell code.
const applyScript = `[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ops = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('%s')) | ConvertFrom-Json
$names = @{ path = 'ExclusionPath'; process = 'ExclusionProcess'; extension = 'ExclusionExtension' }
$errors = [ordered]@{}
foreach ($kind in @('path', 'process', 'extension')) {
  foreach ($value in @($ops.remove.$kind)) {
    if (-not $value) { continue }
    try { $arguments = @{ $names[$kind] = [string]$value }; Remove-MpPreference @arguments }
    catch { $errors[('remove|{0}|{1}' -f $kind, $value)] = $_.Exception.Message }
  }
  foreach ($value in @($ops.add.$kind)) {
    if (-not $value) { continue }
    try { $arguments = @{ $names[$kind] = [string]$value }; Add-MpPreference @arguments }
    catch { $errors[('add|{0}|{1}' -f $kind, $value)] = $_.Exception.Message }
  }
}
if ($ops.schedule) {
  try {
    Set-MpPreference -ScanParameters ([int]$ops.schedule.parameters) -ScanScheduleDay ([int]$ops.schedule.day) -ScanScheduleTime ([datetime]::Today.AddMinutes([int]$ops.schedule.minutes))
  } catch { $errors['schedule'] = $_.Exception.Message }
}
[ordered]@{ errors = $errors } | ConvertTo-Json -Depth 3 -Compress`

func applyOperations(ctx context.Context, ops operations) (map[string]string, error) {
	payload, err := json.Marshal(ops)
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	out, err := runPowerShell(ctx, fmt.Sprintf(applyScript, base64.StdEncoding.EncodeToString(payload)))
	if err != nil {
		return nil, fmt.Errorf("apply Microsoft Defender policy: %w", err)
	}
	var result struct {
		Errors map[string]string `json:"errors"`
	}
	if err := json.Unmarshal(trimOutput(out), &result); err != nil {
		return nil, fmt.Errorf("decode Microsoft Defender policy result: %w", err)
	}
	return result.Errors, nil
}

func trimOutput(out []byte) []byte {
	return bytes.TrimSpace(bytes.TrimPrefix(out, []byte{0xef, 0xbb, 0xbf}))
}
