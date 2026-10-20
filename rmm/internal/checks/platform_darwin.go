//go:build darwin

package checks

import (
	"context"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func command(args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, args[0], args[1:]...).Output()
	return string(output), err
}

// topSampler reads CPU load from top, which samples for a second.
type topSampler struct{}

func newCPUSampler() cpuSampler { return topSampler{} }

func (topSampler) Percent() (float64, bool) {
	output, err := command("top", "-l", "2", "-n", "0", "-s", "1")
	if err != nil {
		return 0, false
	}
	return parseTopCPU(output)
}

func readMemory() (uint64, uint64, error) {
	output, err := command("sysctl", "-n", "hw.memsize")
	if err != nil {
		return 0, 0, err
	}
	total, err := strconv.ParseUint(strings.TrimSpace(output), 10, 64)
	if err != nil {
		return 0, 0, err
	}
	stats, err := command("vm_stat")
	if err != nil {
		return 0, 0, err
	}
	available, err := parseVMStat(stats)
	return total, available, err
}

// readDisks reports the startup disk's data volume and other local volumes.
func readDisks() ([]client.DiskUsage, error) {
	count, err := syscall.Getfsstat(nil, 1 /* MNT_WAIT */)
	if err != nil {
		return nil, err
	}
	stats := make([]syscall.Statfs_t, count)
	if _, err := syscall.Getfsstat(stats, 1); err != nil {
		return nil, err
	}
	var disks []client.DiskUsage
	seen := map[string]bool{}
	for _, stat := range stats {
		mount := cString(stat.Mntonname[:])
		device := cString(stat.Mntfromname[:])
		local := stat.Flags&0x1000 != 0 // MNT_LOCAL
		if !local || seen[device] || stat.Blocks == 0 {
			continue
		}
		if mount != "/" && mount != "/System/Volumes/Data" && !strings.HasPrefix(mount, "/Volumes/") {
			continue
		}
		seen[device] = true
		size := uint64(stat.Bsize)
		disks = append(disks, client.DiskUsage{Name: mount, TotalBytes: stat.Blocks * size, FreeBytes: stat.Bavail * size})
	}
	return disks, nil
}

func cString(raw []int8) string {
	buf := make([]byte, 0, len(raw))
	for _, c := range raw {
		if c == 0 {
			break
		}
		buf = append(buf, byte(c))
	}
	return string(buf)
}

func readNetwork() (map[string]netCounters, error) {
	output, err := command("netstat", "-ibn")
	if err != nil {
		return nil, err
	}
	return parseNetstat(output), nil
}

func serviceStatus(context.Context, string) (string, error) { return "unsupported", nil }
func startService(context.Context, string) error            { return nil }
func restartService(context.Context, string) error          { return nil }
func stopService(context.Context, string) error             { return nil }

func processNames() ([][]string, error) {
	output, err := command("ps", "-axo", "comm=")
	if err != nil {
		return nil, err
	}
	return parsePsComm(output), nil
}
