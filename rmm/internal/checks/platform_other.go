//go:build !linux && !darwin && !windows

package checks

import (
	"context"
	"errors"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

var errUnsupported = errors.New("not supported on this system")

type noSampler struct{}

func (noSampler) Percent() (float64, bool) { return 0, false }

func newCPUSampler() cpuSampler                             { return noSampler{} }
func readMemory() (uint64, uint64, error)                   { return 0, 0, errUnsupported }
func readDisks() ([]client.DiskUsage, error)                { return nil, errUnsupported }
func readNetwork() (map[string]netCounters, error)          { return nil, errUnsupported }
func serviceStatus(context.Context, string) (string, error) { return "unsupported", nil }
func startService(context.Context, string) error            { return errUnsupported }
func restartService(context.Context, string) error          { return errUnsupported }
func stopService(context.Context, string) error             { return errUnsupported }
