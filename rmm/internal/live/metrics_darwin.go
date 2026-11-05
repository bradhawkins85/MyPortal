package live

import (
	"context"
	"errors"
	"os/exec"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

func command(name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, args...).Output()
	return string(out), err
}

// macOS has no cgo-free per-processor counters, so CPU use is the sum of
// every process's recent share from ps.
func readCPUTimes() ([]cpuTimes, error) { return nil, nil }

func directCPUPercent() (float64, bool) {
	out, err := command("/bin/ps", "-A", "-o", "%cpu=")
	if err != nil {
		return 0, false
	}
	var total float64
	for _, line := range strings.Split(out, "\n") {
		value, err := strconv.ParseFloat(strings.TrimSpace(strings.ReplaceAll(line, ",", ".")), 64)
		if err == nil {
			total += value
		}
	}
	percent := total / float64(runtime.NumCPU())
	return min(percent, 100), true
}

var vmStatLine = regexp.MustCompile(`^(.+?):\s+(\d+)\.?$`)

func readMemoryInfo() (memoryInfo, error) {
	raw, err := command("/usr/sbin/sysctl", "-n", "hw.memsize")
	if err != nil {
		return memoryInfo{}, err
	}
	total, err := strconv.ParseUint(strings.TrimSpace(raw), 10, 64)
	if err != nil || total == 0 {
		return memoryInfo{}, errors.New("hw.memsize unreadable")
	}
	stats, err := command("/usr/bin/vm_stat")
	if err != nil {
		return memoryInfo{}, err
	}
	info := memoryInfo{total: total}
	if free, ok := parseVMStatFree(stats); ok && free <= total {
		info.used = total - free
	}
	if swap, err := command("/usr/sbin/sysctl", "-n", "vm.swapusage"); err == nil {
		info.swapTotal, info.swapUsed = parseSwapUsage(swap)
	}
	return info, nil
}

// parseVMStatFree is the bytes vm_stat reports free, inactive or speculative.
func parseVMStatFree(text string) (uint64, bool) {
	pageSize := uint64(4096)
	if match := regexp.MustCompile(`page size of (\d+) bytes`).FindStringSubmatch(text); match != nil {
		if value, err := strconv.ParseUint(match[1], 10, 64); err == nil {
			pageSize = value
		}
	}
	var pages uint64
	found := false
	for _, line := range strings.Split(text, "\n") {
		match := vmStatLine.FindStringSubmatch(strings.TrimSpace(line))
		if match == nil {
			continue
		}
		switch match[1] {
		case "Pages free", "Pages inactive", "Pages speculative":
			value, _ := strconv.ParseUint(match[2], 10, 64)
			pages += value
			found = true
		}
	}
	return pages * pageSize, found
}

var swapField = regexp.MustCompile(`(total|used)\s*=\s*([\d.]+)([KMG])`)

func parseSwapUsage(text string) (uint64, uint64) {
	var total, used uint64
	for _, match := range swapField.FindAllStringSubmatch(text, -1) {
		value, err := strconv.ParseFloat(match[2], 64)
		if err != nil {
			continue
		}
		scale := map[string]float64{"K": 1 << 10, "M": 1 << 20, "G": 1 << 30}[match[3]]
		bytes := uint64(value * scale)
		if match[1] == "total" {
			total = bytes
		} else {
			used = bytes
		}
	}
	return total, used
}

func readNetTotals() (uint64, uint64, error) {
	out, err := command("/usr/sbin/netstat", "-ibn")
	if err != nil {
		return 0, 0, err
	}
	rx, tx := parseNetstatTotals(out)
	return rx, tx, nil
}

// parseNetstatTotals sums Ibytes and Obytes from the link rows of
// netstat -ibn, leaving out loopback.
func parseNetstatTotals(text string) (uint64, uint64) {
	lines := strings.Split(text, "\n")
	if len(lines) == 0 {
		return 0, 0
	}
	header := strings.Fields(lines[0])
	in, out := -1, -1
	for i, name := range header {
		switch name {
		case "Ibytes":
			in = i
		case "Obytes":
			out = i
		}
	}
	if in < 0 || out < 0 {
		return 0, 0
	}
	var rx, tx uint64
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < len(header) || strings.HasPrefix(fields[0], "lo") || !strings.HasPrefix(fields[2], "<Link#") {
			continue
		}
		// Link rows have no address column when the interface has no MAC.
		offset := len(fields) - len(header)
		a, err1 := strconv.ParseUint(fields[in+offset], 10, 64)
		b, err2 := strconv.ParseUint(fields[out+offset], 10, 64)
		if err1 == nil && err2 == nil {
			rx += a
			tx += b
		}
	}
	return rx, tx
}

func processCount() int {
	out, err := command("/bin/ps", "-A", "-o", "pid=")
	if err != nil {
		return 0
	}
	return len(strings.Fields(out))
}

var bootTime = regexp.MustCompile(`sec\s*=\s*(\d+)`)

func uptimeSeconds() uint64 {
	out, err := command("/usr/sbin/sysctl", "-n", "kern.boottime")
	if err != nil {
		return 0
	}
	match := bootTime.FindStringSubmatch(out)
	if match == nil {
		return 0
	}
	sec, err := strconv.ParseInt(match[1], 10, 64)
	if err != nil {
		return 0
	}
	elapsed := time.Since(time.Unix(sec, 0)).Seconds()
	if elapsed < 0 {
		return 0
	}
	return uint64(elapsed)
}
