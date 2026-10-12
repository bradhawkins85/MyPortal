//go:build linux

package checks

import (
	"context"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func newCPUSampler() cpuSampler {
	return &timesSampler{read: func() (cpuTimes, error) {
		data, err := os.ReadFile("/proc/stat")
		if err != nil {
			return cpuTimes{}, err
		}
		return parseProcStat(string(data))
	}}
}

func readMemory() (uint64, uint64, error) {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, 0, err
	}
	return parseMeminfo(string(data))
}

func readDisks() ([]client.DiskUsage, error) {
	data, err := os.ReadFile("/proc/mounts")
	if err != nil {
		return nil, err
	}
	return statfsDisks(parseMounts(string(data))), nil
}

func statfsDisks(mounts []string) []client.DiskUsage {
	var disks []client.DiskUsage
	for _, mount := range mounts {
		var stat syscall.Statfs_t
		if err := syscall.Statfs(mount, &stat); err != nil || stat.Blocks == 0 {
			continue
		}
		size := uint64(stat.Bsize)
		disks = append(disks, client.DiskUsage{Name: mount, TotalBytes: stat.Blocks * size, FreeBytes: stat.Bavail * size})
	}
	return disks
}

func readNetwork() (map[string]netCounters, error) {
	data, err := os.ReadFile("/proc/net/dev")
	if err != nil {
		return nil, err
	}
	return parseNetDev(string(data)), nil
}

func systemctl(ctx context.Context, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, time.Minute)
	defer cancel()
	output, err := exec.CommandContext(ctx, "systemctl", args...).CombinedOutput()
	return strings.TrimSpace(string(output)), err
}

// serviceStatus reports a systemd unit as running, stopped or missing.
func serviceStatus(ctx context.Context, name string) (string, error) {
	if _, err := exec.LookPath("systemctl"); err != nil {
		return "unsupported", nil
	}
	load, _ := systemctl(ctx, "show", "--property=LoadState", "--value", "--", name)
	if load == "not-found" {
		return "missing", nil
	}
	state, _ := systemctl(ctx, "is-active", "--", name)
	switch state {
	case "active", "reloading":
		return "running", nil
	case "activating":
		return "start_pending", nil
	case "deactivating":
		return "stop_pending", nil
	case "inactive", "failed":
		return "stopped", nil
	}
	return "unknown", nil
}

func controlUnit(ctx context.Context, verb, name string) error {
	output, err := systemctl(ctx, verb, "--", name)
	if err != nil && output != "" {
		return &commandError{output}
	}
	return err
}

func startService(ctx context.Context, name string) error   { return controlUnit(ctx, "start", name) }
func restartService(ctx context.Context, name string) error { return controlUnit(ctx, "restart", name) }
func stopService(ctx context.Context, name string) error    { return controlUnit(ctx, "stop", name) }

type commandError struct{ output string }

func (e *commandError) Error() string { return lastLine(e.output) }
