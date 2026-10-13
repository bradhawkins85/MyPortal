//go:build linux

package thermal

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func TestLinuxSensorsAndThermalFallback(t *testing.T) {
	root := t.TempDir()
	write := func(name, data string) {
		path := filepath.Join(root, name)
		if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
	}
	write("class/thermal/thermal_zone0/temp", "42000")
	write("class/thermal/thermal_zone0/type", "acpitz")
	s := &Snapshot{}
	collectLinux(context.Background(), root, s)
	if len(s.Sensors) != 1 || s.Sensors[0].Celsius != 42 {
		t.Fatalf("fallback: %+v", s)
	}
	write("class/hwmon/hwmon0/temp1_input", "63500")
	write("class/hwmon/hwmon0/temp1_label", "CPU Package")
	write("class/hwmon/hwmon0/name", "coretemp")
	write("class/hwmon/hwmon0/temp2_input", "unavailable")
	s = &Snapshot{}
	collectLinux(context.Background(), root, s)
	if len(s.Sensors) != 1 || s.Sensors[0].Celsius != 63.5 || s.Sensors[0].Name != "CPU Package" || s.Sensors[0].Hardware != "coretemp" {
		t.Fatalf("hwmon: %+v", s)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	s = &Snapshot{}
	collectLinux(ctx, root, s)
	if len(s.Sensors) != 0 {
		t.Fatal("collection ignored cancellation")
	}
}
