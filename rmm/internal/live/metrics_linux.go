package live

import (
	"errors"
	"os"
	"strconv"
	"strings"
)

func readCPUTimes() ([]cpuTimes, error) {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return nil, err
	}
	return parseProcStatCPUs(string(data))
}

// parseProcStatCPUs reads the "cpu" and "cpuN" lines of /proc/stat. Busy
// time is everything but idle and iowait; guest time is already counted in
// user time, so only the first eight columns are summed.
func parseProcStatCPUs(text string) ([]cpuTimes, error) {
	var out []cpuTimes
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 5 || !strings.HasPrefix(fields[0], "cpu") {
			continue
		}
		var values []uint64
		for _, field := range fields[1:] {
			value, err := strconv.ParseUint(field, 10, 64)
			if err != nil {
				break
			}
			values = append(values, value)
		}
		if len(values) < 4 {
			continue
		}
		if len(values) > 8 {
			values = values[:8]
		}
		var total uint64
		for _, value := range values {
			total += value
		}
		idle := values[3]
		if len(values) > 4 {
			idle += values[4]
		}
		out = append(out, cpuTimes{busy: total - idle, total: total})
	}
	if len(out) == 0 {
		return nil, errors.New("no cpu lines in /proc/stat")
	}
	return out, nil
}

func directCPUPercent() (float64, bool) { return 0, false }

func readMemoryInfo() (memoryInfo, error) {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return memoryInfo{}, err
	}
	return parseMeminfoAll(string(data))
}

func parseMeminfoAll(text string) (memoryInfo, error) {
	values := map[string]uint64{}
	for _, line := range strings.Split(text, "\n") {
		name, rest, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		fields := strings.Fields(rest)
		if len(fields) == 0 {
			continue
		}
		value, err := strconv.ParseUint(fields[0], 10, 64)
		if err != nil {
			continue
		}
		values[name] = value * 1024
	}
	total, ok := values["MemTotal"]
	if !ok || total == 0 {
		return memoryInfo{}, errors.New("MemTotal missing from /proc/meminfo")
	}
	available, ok := values["MemAvailable"]
	if !ok {
		available = values["MemFree"] + values["Buffers"] + values["Cached"]
	}
	if available > total {
		available = total
	}
	info := memoryInfo{total: total, used: total - available, swapTotal: values["SwapTotal"]}
	if free := values["SwapFree"]; free <= info.swapTotal {
		info.swapUsed = info.swapTotal - free
	}
	return info, nil
}

func readNetTotals() (uint64, uint64, error) {
	data, err := os.ReadFile("/proc/net/dev")
	if err != nil {
		return 0, 0, err
	}
	rx, tx := parseNetDevTotals(string(data))
	return rx, tx, nil
}

// parseNetDevTotals sums received and sent bytes over every interface but
// loopback.
func parseNetDevTotals(text string) (uint64, uint64) {
	var rx, tx uint64
	for _, line := range strings.Split(text, "\n") {
		name, rest, ok := strings.Cut(line, ":")
		if !ok || strings.TrimSpace(name) == "lo" {
			continue
		}
		fields := strings.Fields(rest)
		if len(fields) < 9 {
			continue
		}
		in, err1 := strconv.ParseUint(fields[0], 10, 64)
		out, err2 := strconv.ParseUint(fields[8], 10, 64)
		if err1 != nil || err2 != nil {
			continue
		}
		rx += in
		tx += out
	}
	return rx, tx
}

func processCount() int {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return 0
	}
	count := 0
	for _, entry := range entries {
		if _, err := strconv.Atoi(entry.Name()); err == nil && entry.IsDir() {
			count++
		}
	}
	return count
}

func uptimeSeconds() uint64 {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 0
	}
	fields := strings.Fields(string(data))
	if len(fields) == 0 {
		return 0
	}
	value, err := strconv.ParseFloat(fields[0], 64)
	if err != nil || value < 0 {
		return 0
	}
	return uint64(value)
}
