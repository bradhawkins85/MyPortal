//go:build windows

package healthcheck

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

// These run the real, read-only collectors on a Windows host (the MSI build
// runs them after bundling the sensor library). PawnIO is never installed.

func TestHealthScriptProducesSnapshot(t *testing.T) {
	if testing.Short() {
		t.Skip("runs the health snapshot script")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	snapshot, err := collectPlatformSnapshot(ctx, DefaultEventDays)
	if err != nil {
		t.Fatal(err)
	}
	var sections map[string]json.RawMessage
	if err := json.Unmarshal(snapshot.Sections, &sections); err != nil {
		t.Fatalf("sections are not a JSON object: %v", err)
	}
	for _, name := range []string{"system", "cpu", "memory", "volumes", "chkdsk", "stability", "updates"} {
		if _, ok := sections[name]; !ok {
			t.Errorf("section %q missing (errors: %v)", name, snapshot.Errors)
		}
	}
	var volumes []map[string]any
	if err := json.Unmarshal(sections["volumes"], &volumes); err != nil || len(volumes) == 0 {
		t.Fatalf("volumes must be a non-empty list: %s (%v)", sections["volumes"], err)
	}
	t.Logf("snapshot took %d ms; sections that failed: %v", snapshot.ElapsedMS, snapshot.Errors)
}

func TestSensorBundleLoadsInWindowsPowerShell(t *testing.T) {
	if _, err := bundleFiles(); err != nil {
		t.Skipf("prepare the bundle with sensor-bundle/build.py: %v", err)
	}
	dir, cleanup, err := extractBundle(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer cleanup()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	// Without PawnIO the library still loads and reads what it can.
	readings, err := readSensors(ctx, dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("%d sensor readings without PawnIO", len(readings))
}

func TestIdentityReadsTheBIOSKey(t *testing.T) {
	manufacturer, model, err := platformIdentity()
	if err != nil || manufacturer == "" || model == "" {
		t.Fatalf("identity %q %q %v", manufacturer, model, err)
	}
}
