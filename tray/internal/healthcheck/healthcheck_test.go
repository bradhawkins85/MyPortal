package healthcheck

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/agent"
)

func testRunner(t *testing.T) (*Runner, *[]string) {
	t.Helper()
	var stages []string
	now := time.Date(2026, 10, 23, 1, 0, 0, 0, time.UTC)
	r := &Runner{
		StateDir:     t.TempDir(),
		AgentVersion: "v-test",
		platform:     "windows",
		OnProgress:   func(stage, _ string) { stages = append(stages, stage) },
		now:          func() time.Time { now = now.Add(time.Second); return now },
		identify:     func() (string, string, error) { return "Dell Inc.", "Latitude 7440", nil },
		diskSMART: func(context.Context) DiskSMARTReport {
			return DiskSMARTReport{Status: SMARTError, Source: smartSource, Detail: "Chocolatey could not be installed.", Disks: []SMARTDisk{}}
		},
		collectSnapshot: func(_ context.Context, days int) (platformSnapshot, error) {
			if days != DefaultEventDays {
				t.Errorf("event days %d", days)
			}
			return platformSnapshot{
				Schema:   1,
				Sections: json.RawMessage(`{"volumes":[{"drive":"C:","free_percent":42}]}`),
				Errors:   map[string]string{"battery": "no battery"},
			}, nil
		},
		sensors: func(_ context.Context, allow bool, identity machineIdentity) (SensorReport, PawnIOReport) {
			if !allow || identity.Model != "Latitude 7440" {
				t.Errorf("sensors called with allow=%v identity=%+v", allow, identity)
			}
			return SensorReport{Status: "available", Readings: []SensorReading{}},
				PawnIOReport{Status: PawnIOInstalledAndRemoved}
		},
		newAgent: func() *agent.Agent {
			return &agent.Agent{
				AllowedSource: func(source string) bool { return source == "System" },
				CollectLogs: func(_ context.Context, specs []agent.LogSpec) (string, []string, error) {
					if len(specs) != 1 || specs[0].Source != "System" || specs[0].Window != 72*time.Hour {
						t.Errorf("specs %+v", specs)
					}
					return "### System Log\n2026-10-22 [Error] id=7: bad block\n", []string{"System"}, nil
				},
			}
		},
	}
	return r, &stages
}

func TestRunSnapshotPhase(t *testing.T) {
	r, stages := testRunner(t)
	result, err := r.Run(context.Background(), Request{
		CommandID: 7, HealthCheckID: 3, Phase: PhaseSnapshot, AllowPawnIO: true,
		LogRequests: []agent.LogRequest{{Source: "System", Reason: "disk errors", Hours: 72}},
	})
	if err != nil {
		t.Fatal(err)
	}
	var snapshot map[string]any
	if err := json.Unmarshal(result.Snapshot, &snapshot); err != nil {
		t.Fatal(err)
	}
	if snapshot["platform"] != "windows" || snapshot["is_virtual"] != false || snapshot["agent_version"] != "v-test" {
		t.Fatalf("snapshot header %v", snapshot)
	}
	volumes := snapshot["sections"].(map[string]any)["volumes"].([]any)
	if len(volumes) != 1 {
		t.Fatalf("volumes %v", volumes)
	}
	if snapshot["pawnio"].(map[string]any)["status"] != PawnIOInstalledAndRemoved {
		t.Fatalf("pawnio %v", snapshot["pawnio"])
	}
	if snapshot["errors"].(map[string]any)["battery"] != "no battery" {
		t.Fatalf("errors %v", snapshot["errors"])
	}
	if snapshot["disk_smart"].(map[string]any)["status"] != SMARTError ||
		snapshot["errors"].(map[string]any)["disk_smart"] != "Chocolatey could not be installed." {
		t.Fatalf("disk_smart %v errors %v", snapshot["disk_smart"], snapshot["errors"])
	}
	if len(result.LogBundle) == 0 {
		t.Fatal("no log bundle")
	}
	got := strings.Join(*stages, ",")
	if got != "collecting_snapshot,reading_smart,reading_sensors,collecting_logs" {
		t.Fatalf("stages %s", got)
	}
}

func TestRunSkipsCrystalDiskInfoOnVirtualMachines(t *testing.T) {
	r, stages := testRunner(t)
	r.identify = func() (string, string, error) { return "Microsoft Corporation", "Virtual Machine", nil }
	r.diskSMART = func(context.Context) DiskSMARTReport {
		t.Fatal("CrystalDiskInfo run on a virtual machine")
		return DiskSMARTReport{}
	}
	r.sensors = func(context.Context, bool, machineIdentity) (SensorReport, PawnIOReport) {
		return SensorReport{Status: "unavailable"}, PawnIOReport{Status: PawnIOSkippedVirtual}
	}
	result, err := r.Run(context.Background(), Request{Phase: PhaseSnapshot})
	if err != nil {
		t.Fatal(err)
	}
	var snapshot Snapshot
	if err := json.Unmarshal(result.Snapshot, &snapshot); err != nil {
		t.Fatal(err)
	}
	if snapshot.DiskSMART == nil || snapshot.DiskSMART.Status != SMARTSkippedVirtual {
		t.Fatalf("disk_smart %+v", snapshot.DiskSMART)
	}
	if strings.Contains(strings.Join(*stages, ","), StageReadingSMART) {
		t.Fatalf("stages %v", *stages)
	}
}

func TestRunLogsPhaseSkipsTheSnapshot(t *testing.T) {
	r, _ := testRunner(t)
	r.collectSnapshot = func(context.Context, int) (platformSnapshot, error) {
		t.Fatal("snapshot collected in the logs phase")
		return platformSnapshot{}, nil
	}
	r.diskSMART = func(context.Context) DiskSMARTReport {
		t.Fatal("SMART data read in the logs phase")
		return DiskSMARTReport{}
	}
	r.sensors = func(context.Context, bool, machineIdentity) (SensorReport, PawnIOReport) {
		t.Fatal("sensors read in the logs phase")
		return SensorReport{}, PawnIOReport{}
	}
	result, err := r.Run(context.Background(), Request{
		Phase: PhaseLogs, LogRequests: []agent.LogRequest{{Source: "System", Hours: 72}},
	})
	if err != nil || result.Snapshot != nil || len(result.LogBundle) == 0 {
		t.Fatalf("result %+v err %v", result, err)
	}
}

func TestRunReportsSnapshotFailuresButStillSendsWhatItHas(t *testing.T) {
	r, _ := testRunner(t)
	r.collectSnapshot = func(context.Context, int) (platformSnapshot, error) {
		return platformSnapshot{}, errors.New("powershell missing")
	}
	result, err := r.Run(context.Background(), Request{Phase: PhaseSnapshot, AllowPawnIO: true})
	if err == nil || !strings.Contains(err.Error(), "powershell missing") {
		t.Fatalf("error %v", err)
	}
	var snapshot Snapshot
	if json.Unmarshal(result.Snapshot, &snapshot) != nil || string(snapshot.Sections) != "{}" ||
		!strings.Contains(snapshot.Errors["snapshot"], "powershell missing") {
		t.Fatalf("snapshot %s", result.Snapshot)
	}
}

func TestRunRejectsAnUnknownPhase(t *testing.T) {
	r, _ := testRunner(t)
	if _, err := r.Run(context.Background(), Request{Phase: "install"}); err == nil {
		t.Fatal("unknown phase accepted")
	}
}

func TestCleanReadings(t *testing.T) {
	nan, value, inf := math.NaN(), 40.0, math.Inf(1)
	readings := cleanReadings([]SensorReading{
		{Name: "missing"},
		{Name: "nan", Value: &nan},
		{Name: strings.Repeat("x", 300), Value: &value, Max: &inf},
	})
	if len(readings) != 1 || len([]rune(readings[0].Name)) != 120 || readings[0].Max != nil {
		t.Fatalf("readings %+v", readings)
	}
}
