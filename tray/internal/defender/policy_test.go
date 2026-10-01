package defender

import (
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

func boolPtr(v bool) *bool { return &v }
func intPtr(v int) *int    { return &v }

// fakeEndpoint emulates Defender preferences, optionally discarding changes the
// way Tamper Protection or a management policy silently does.
type fakeEndpoint struct {
	env      environment
	discard  bool
	applied  []operations
	probeErr error
}

func (f *fakeEndpoint) install(t *testing.T) {
	t.Helper()
	previousProbe, previousApply := probeFunc, applyFunc
	probeFunc = func(context.Context) (environment, error) {
		if f.probeErr != nil {
			return environment{}, f.probeErr
		}
		env := f.env
		env.ExclusionPath = append([]string(nil), f.env.ExclusionPath...)
		env.ExclusionProcess = append([]string(nil), f.env.ExclusionProcess...)
		env.ExclusionExtension = append([]string(nil), f.env.ExclusionExtension...)
		return env, nil
	}
	applyFunc = func(_ context.Context, ops operations) (map[string]string, error) {
		f.applied = append(f.applied, ops)
		if f.discard {
			return nil, nil
		}
		for _, value := range ops.Add["path"] {
			f.env.ExclusionPath = append(f.env.ExclusionPath, value)
		}
		for _, value := range ops.Add["process"] {
			f.env.ExclusionProcess = append(f.env.ExclusionProcess, value)
		}
		for _, value := range ops.Remove["path"] {
			f.env.ExclusionPath = removeFold(f.env.ExclusionPath, value)
		}
		if ops.Schedule != nil {
			f.env.ScanParameters, f.env.ScanScheduleDay, f.env.ScanScheduleMinutes = ops.Schedule.Parameters, ops.Schedule.Day, ops.Schedule.Minutes
		}
		return nil, nil
	}
	t.Cleanup(func() { probeFunc, applyFunc = previousProbe, previousApply })
}

func statuses(result *api.DefenderPolicyResult) map[string]string {
	out := map[string]string{}
	for _, item := range result.Items {
		out[item.Action+" "+item.Value] = item.Status
	}
	return out
}

func TestApplyPolicyAddsExclusionsAndSchedule(t *testing.T) {
	endpoint := &fakeEndpoint{env: environment{ExclusionsReadable: true, ExclusionPath: []string{`C:\Local`}, ScanParameters: 1}}
	endpoint.install(t)
	statePath := filepath.Join(t.TempDir(), "state.json")
	policy := api.DefenderPolicy{Enabled: true,
		Exclusions:    []api.DefenderExclusion{{Type: "path", Value: `C:\Trusted`}, {Type: "process", Value: "agent.exe"}, {Type: "path", Value: `c:\local`}},
		ScheduledScan: &api.DefenderScheduledScan{Type: "full", Day: intPtr(0), Time: "02:30"}}

	result, err := ApplyPolicy(context.Background(), policy, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != "applied" {
		t.Fatalf("status = %q, items %+v", result.Status, result.Items)
	}
	if endpoint.env.ScanParameters != 2 || endpoint.env.ScanScheduleDay != 2 || endpoint.env.ScanScheduleMinutes != 150 {
		t.Fatalf("schedule not applied as full scan on Monday 02:30: %+v", endpoint.env)
	}
	state := loadPolicyState(statePath)
	if len(state.Exclusions["path"]) != 1 || state.Exclusions["path"][0] != `C:\Trusted` {
		t.Fatalf("agent must only own the exclusions it added: %+v", state.Exclusions)
	}

	// A second run with nothing to change makes no calls at all.
	endpoint.applied = nil
	if _, err := ApplyPolicy(context.Background(), policy, statePath); err != nil {
		t.Fatal(err)
	}
	if len(endpoint.applied) != 0 {
		t.Fatalf("unexpected changes on a converged endpoint: %+v", endpoint.applied)
	}

	// Removing the policy removes only what the agent added and restores the
	// original schedule.
	result, err = ApplyPolicy(context.Background(), api.DefenderPolicy{Enabled: true}, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if containsFold(endpoint.env.ExclusionPath, `C:\Trusted`) || !containsFold(endpoint.env.ExclusionPath, `C:\Local`) {
		t.Fatalf("unexpected exclusions after removal: %v", endpoint.env.ExclusionPath)
	}
	if endpoint.env.ScanParameters != 1 || endpoint.env.ScanScheduleDay != 0 || endpoint.env.ScanScheduleMinutes != 0 {
		t.Fatalf("original schedule was not restored: %+v", endpoint.env)
	}
	if got := statuses(result)[`remove C:\Trusted`]; got != StatusRemoved {
		t.Fatalf("remove status = %q", got)
	}
}

func TestTamperProtectedExclusionsAreNeverAttempted(t *testing.T) {
	for name, protects := range map[string]*bool{"reported": boolPtr(true), "not reported": nil} {
		t.Run(name, func(t *testing.T) {
			endpoint := &fakeEndpoint{env: environment{ExclusionsReadable: true, TamperProtected: true, TamperProtectedExclusions: protects}}
			endpoint.install(t)
			policy := api.DefenderPolicy{Enabled: true, Exclusions: []api.DefenderExclusion{{Type: "path", Value: `C:\Trusted`}}}

			result, err := ApplyPolicy(context.Background(), policy, filepath.Join(t.TempDir(), "state.json"))
			if err != nil {
				t.Fatal(err)
			}
			if len(endpoint.applied) != 0 {
				t.Fatalf("a tamper-protected exclusion change was attempted: %+v", endpoint.applied)
			}
			if result.Status != "blocked" || result.Items[0].Status != StatusBlockedByTamper || !result.TamperProtection.Enabled {
				t.Fatalf("unexpected result: %+v", result)
			}
		})
	}
}

func TestExclusionsApplyWhenTamperProtectionDoesNotCoverThem(t *testing.T) {
	endpoint := &fakeEndpoint{env: environment{ExclusionsReadable: true, TamperProtected: true, TamperProtectedExclusions: boolPtr(false)}}
	endpoint.install(t)
	policy := api.DefenderPolicy{Enabled: true, Exclusions: []api.DefenderExclusion{{Type: "path", Value: `C:\Trusted`}}}

	result, err := ApplyPolicy(context.Background(), policy, filepath.Join(t.TempDir(), "state.json"))
	if err != nil {
		t.Fatal(err)
	}
	if result.Status != "applied" {
		t.Fatalf("status = %q", result.Status)
	}
}

func TestDiscardedChangeIsReportedAndNotRetried(t *testing.T) {
	endpoint := &fakeEndpoint{env: environment{ExclusionsReadable: true, TamperProtected: true, TamperProtectedExclusions: boolPtr(false)}, discard: true}
	endpoint.install(t)
	statePath := filepath.Join(t.TempDir(), "state.json")
	policy := api.DefenderPolicy{Enabled: true, ScheduledScan: &api.DefenderScheduledScan{Type: "quick", Day: intPtr(6), Time: "23:00"}}

	result, err := ApplyPolicy(context.Background(), policy, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if len(endpoint.applied) != 1 || result.Items[0].Status != StatusBlockedByTamper {
		t.Fatalf("expected one attempt reported as blocked: %d attempts, %+v", len(endpoint.applied), result.Items)
	}
	result, err = ApplyPolicy(context.Background(), policy, statePath)
	if err != nil {
		t.Fatal(err)
	}
	if len(endpoint.applied) != 1 {
		t.Fatal("a change Defender discarded was retried under the same conditions")
	}
	if result.Items[0].Status != StatusBlockedByTamper || !strings.Contains(result.Items[0].Message, "will not retry") {
		t.Fatalf("unexpected recalled item: %+v", result.Items[0])
	}

	// Turning Tamper Protection off makes the change worth one more attempt.
	endpoint.env.TamperProtected = false
	if _, err := ApplyPolicy(context.Background(), policy, statePath); err != nil {
		t.Fatal(err)
	}
	if len(endpoint.applied) != 2 {
		t.Fatalf("attempts = %d, want a retry after the protection state changed", len(endpoint.applied))
	}
}

func TestPolicyManagedSettingsAndUnsupportedTypesAreFlagged(t *testing.T) {
	endpoint := &fakeEndpoint{env: environment{ExclusionsReadable: true, LocalAdminMergeDisabled: true, ScheduleManagedByPolicy: true}}
	endpoint.install(t)
	policy := api.DefenderPolicy{Enabled: true,
		Exclusions:    []api.DefenderExclusion{{Type: "path", Value: `C:\Trusted`}, {Type: "registry", Value: `HKLM\Software\Contoso`}},
		ScheduledScan: &api.DefenderScheduledScan{Type: "quick", Day: intPtr(2), Time: "12:00"}}

	result, err := ApplyPolicy(context.Background(), policy, filepath.Join(t.TempDir(), "state.json"))
	if err != nil {
		t.Fatal(err)
	}
	if len(endpoint.applied) != 0 {
		t.Fatalf("policy-managed settings were changed: %+v", endpoint.applied)
	}
	got := statuses(result)
	if got[`add C:\Trusted`] != StatusManagedByPolicy || got[`add HKLM\Software\Contoso`] != StatusUnsupported ||
		got["set Quick scan Wednesday at 12:00"] != StatusManagedByPolicy || result.Status != "failed" {
		t.Fatalf("unexpected result: %s %+v", result.Status, got)
	}
}

func TestProbeFailureIsReturned(t *testing.T) {
	endpoint := &fakeEndpoint{probeErr: errors.New("Get-MpPreference failed")}
	endpoint.install(t)
	if _, err := ApplyPolicy(context.Background(), api.DefenderPolicy{Enabled: true}, filepath.Join(t.TempDir(), "state.json")); err == nil {
		t.Fatal("expected the probe error")
	}
}

func TestDesiredScheduleMapsPortalDaysToDefenderDays(t *testing.T) {
	for portalDay, defenderDay := range map[int]int{0: 2, 5: 7, 6: 1} {
		got, ok := desiredSchedule(&api.DefenderScheduledScan{Type: "quick", Day: intPtr(portalDay), Time: "01:05"})
		if !ok || got.Day != defenderDay || got.Minutes != 65 {
			t.Errorf("portal day %d -> %+v, want Defender day %d", portalDay, got, defenderDay)
		}
	}
	if _, ok := desiredSchedule(&api.DefenderScheduledScan{Type: "", Day: intPtr(1), Time: "01:00"}); ok {
		t.Error("an unset scan type must leave the schedule unmanaged")
	}
}

func TestApplyScriptCarriesValuesAsEncodedData(t *testing.T) {
	value := `C:\Evil'; Remove-Item C:\ -Recurse; '`
	payload, _ := json.Marshal(operations{Add: map[string][]string{"path": {value}}})
	if strings.Contains(applyScript, value) || !strings.Contains(string(payload), "Evil") {
		t.Fatal("unexpected script construction")
	}
	for _, cmdlet := range []string{"Add-MpPreference", "Remove-MpPreference", "FromBase64String"} {
		if !strings.Contains(applyScript, cmdlet) {
			t.Errorf("apply script is missing %s", cmdlet)
		}
	}
}
