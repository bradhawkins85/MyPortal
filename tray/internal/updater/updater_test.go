package updater

import (
	"testing"
	"time"
)

func TestJitteredInterval(t *testing.T) {
	// jitteredInterval must return a value within ±10 % of UpdateInterval.
	min := time.Duration(float64(UpdateInterval) * (1 - jitterFraction))
	max := time.Duration(float64(UpdateInterval) * (1 + jitterFraction))

	// Run many samples to exercise the full range.
	for i := 0; i < 1000; i++ {
		d := jitteredInterval()
		if d < min || d > max {
			t.Fatalf("jitteredInterval() = %v; want in [%v, %v]", d, min, max)
		}
	}
}

func TestJitteredIntervalNonZero(t *testing.T) {
	d := jitteredInterval()
	if d <= 0 {
		t.Fatalf("jitteredInterval() = %v; want > 0", d)
	}
}

func TestStartupJitterMaxLessThanUpdateInterval(t *testing.T) {
	// startupJitterMax must be less than UpdateInterval to avoid a situation
	// where the startup delay is longer than one full update cycle.
	if startupJitterMax >= UpdateInterval {
		t.Fatalf("startupJitterMax (%v) must be < UpdateInterval (%v)", startupJitterMax, UpdateInterval)
	}
}
