package healthcheck

import (
	"context"
	"math"
	"os"
	"path/filepath"
)

// sensorBundleDirPrefix names the temporary folders the sensor bundle is
// extracted to for one check.
const sensorBundleDirPrefix = "sensor-bundle-"

// maxSensorReadings caps the readings sent to the server.
const maxSensorReadings = 400

// CleanupPawnIO removes PawnIO when a health check installed it and the
// service stopped before removing it, along with any leftover sensor bundle
// folder. Call it once when the service starts. It reports whether a
// leftover installation was found.
func CleanupPawnIO(ctx context.Context, stateDir string) (bool, error) {
	return platformCleanupPawnIO(ctx, stateDir)
}

func removeStaleBundles(stateDir string) {
	if stateDir == "" {
		return
	}
	matches, _ := filepath.Glob(filepath.Join(stateDir, sensorBundleDirPrefix+"*"))
	for _, match := range matches {
		_ = os.RemoveAll(match)
	}
}

// cleanReadings drops readings without a usable value and bounds the rest.
func cleanReadings(readings []SensorReading) []SensorReading {
	clean := make([]SensorReading, 0, len(readings))
	for _, reading := range readings {
		if reading.Value == nil || math.IsNaN(*reading.Value) || math.IsInf(*reading.Value, 0) {
			continue
		}
		for _, field := range []*string{&reading.HardwareType, &reading.Hardware, &reading.Type, &reading.Name, &reading.Unit} {
			*field = truncate(*field, 120)
		}
		for _, bound := range []**float64{&reading.Min, &reading.Max} {
			if *bound != nil && (math.IsNaN(**bound) || math.IsInf(**bound, 0)) {
				*bound = nil
			}
		}
		clean = append(clean, reading)
		if len(clean) == maxSensorReadings {
			break
		}
	}
	return clean
}
