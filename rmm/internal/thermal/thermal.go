// Package thermal collects best-effort temperature snapshots at agent check-in.
package thermal

import (
	"context"
	"math"
	"time"
)

const MaxSensors = 256

type Sensor struct {
	ID       string  `json:"id"`
	Name     string  `json:"name"`
	Hardware string  `json:"hardware"`
	Celsius  float64 `json:"celsius"`
}

type Snapshot struct {
	CollectedAt time.Time `json:"collected_at"`
	Status      string    `json:"status"`
	Sensors     []Sensor  `json:"sensors"`
}

// Collect never prevents a check-in when sensors or permissions are unavailable.
func Collect(ctx context.Context) *Snapshot {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	s := &Snapshot{CollectedAt: time.Now().UTC(), Status: "unavailable", Sensors: []Sensor{}}
	collectPlatform(ctx, s)
	valid := make([]Sensor, 0, len(s.Sensors))
	seen := make(map[string]bool)
	for _, sensor := range s.Sensors {
		if sensor.ID == "" || math.IsNaN(sensor.Celsius) || math.IsInf(sensor.Celsius, 0) || sensor.Celsius < -100 || sensor.Celsius > 250 {
			continue
		}
		sensor.ID = truncate(sensor.ID, 255)
		sensor.Name = truncate(sensor.Name, 255)
		sensor.Hardware = truncate(sensor.Hardware, 255)
		if seen[sensor.ID] {
			continue
		}
		seen[sensor.ID] = true
		valid = append(valid, sensor)
		if len(valid) == MaxSensors {
			break
		}
	}
	s.Sensors = valid
	if len(valid) > 0 {
		s.Status = "available"
	}
	return s
}

func truncate(value string, limit int) string {
	r := []rune(value)
	if len(r) > limit {
		return string(r[:limit])
	}
	return value
}
