//go:build !windows && !darwin

package healthcheck

import (
	"context"
	"errors"
)

const platformName = "unknown"

var errUnsupported = errors.New("health checks are not supported on this platform")

func collectPlatformSnapshot(context.Context, int) (platformSnapshot, error) {
	return platformSnapshot{}, errUnsupported
}

func platformIdentity() (string, string, error) { return "", "", errUnsupported }

func platformSensors(context.Context, string, bool, machineIdentity) (SensorReport, PawnIOReport) {
	return SensorReport{Status: "unavailable", Detail: errUnsupported.Error(), Readings: []SensorReading{}},
		PawnIOReport{Status: PawnIOSkippedUnsupported}
}

func platformDiskSMART(context.Context) DiskSMARTReport {
	return DiskSMARTReport{Status: SMARTUnavailable, Source: smartSource, Detail: errUnsupported.Error(), Disks: []SMARTDisk{}}
}

func platformCleanupPawnIO(context.Context, string) (bool, error) { return false, nil }
