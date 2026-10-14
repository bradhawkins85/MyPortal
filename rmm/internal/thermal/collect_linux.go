//go:build linux

package thermal

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func collectPlatform(ctx context.Context, s *Snapshot) { collectLinux(ctx, "/sys", s) }

func readText(path string) string {
	data, _ := os.ReadFile(path)
	return strings.TrimSpace(string(data))
}

func collectLinux(ctx context.Context, root string, s *Snapshot) {
	// hwmon exposes CPU, GPU, board and drive temperatures in millidegrees C.
	paths, _ := filepath.Glob(filepath.Join(root, "class/hwmon/hwmon*/temp*_input"))
	for _, path := range paths {
		if ctx.Err() != nil || len(s.Sensors) >= MaxSensors {
			return
		}
		value, err := strconv.ParseFloat(readText(path), 64)
		if err != nil {
			continue
		}
		base := strings.TrimSuffix(path, "_input")
		name := readText(base + "_label")
		if name == "" {
			name = filepath.Base(base)
		}
		hardware := readText(filepath.Join(filepath.Dir(path), "name"))
		id, _ := filepath.Rel(root, path)
		s.Sensors = append(s.Sensors, Sensor{ID: id, Name: name, Hardware: hardware, Celsius: value / 1000})
	}
	// Some endpoints only expose ACPI thermal zones; avoid duplicating hwmon.
	if len(s.Sensors) > 0 {
		return
	}
	paths, _ = filepath.Glob(filepath.Join(root, "class/thermal/thermal_zone*/temp"))
	for _, path := range paths {
		if ctx.Err() != nil || len(s.Sensors) >= MaxSensors {
			return
		}
		value, err := strconv.ParseFloat(readText(path), 64)
		if err != nil {
			continue
		}
		name := readText(filepath.Join(filepath.Dir(path), "type"))
		id, _ := filepath.Rel(root, path)
		s.Sensors = append(s.Sensors, Sensor{ID: id, Name: name, Hardware: "Thermal zone", Celsius: value / 1000})
	}
}
