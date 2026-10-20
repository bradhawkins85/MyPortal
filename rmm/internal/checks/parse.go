package checks

import (
	"bufio"
	"errors"
	"strconv"
	"strings"
)

// The parsers below read the text Linux and macOS give out, so they are
// tested on every platform.

// parseProcStat reads the first "cpu" line of Linux's /proc/stat.
func parseProcStat(text string) (cpuTimes, error) {
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 5 || fields[0] != "cpu" {
			continue
		}
		var times cpuTimes
		// user nice system idle iowait irq softirq steal; guest time is
		// already counted in user.
		for i, field := range fields[1:] {
			if i >= 8 {
				break
			}
			value, err := strconv.ParseUint(field, 10, 64)
			if err != nil {
				return cpuTimes{}, err
			}
			times.total += value
			if i == 3 || i == 4 {
				times.idle += value
			}
		}
		return times, nil
	}
	return cpuTimes{}, errors.New("no cpu line in /proc/stat")
}

// parseMeminfo reads MemTotal and MemAvailable (in kB) from /proc/meminfo.
func parseMeminfo(text string) (total, available uint64, err error) {
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
		if value, err := strconv.ParseUint(fields[0], 10, 64); err == nil {
			values[name] = value * 1024
		}
	}
	total = values["MemTotal"]
	available, ok := values["MemAvailable"]
	if !ok {
		available = values["MemFree"] + values["Buffers"] + values["Cached"]
	}
	if total == 0 {
		return 0, 0, errors.New("no MemTotal in /proc/meminfo")
	}
	return total, available, nil
}

// parseNetDev reads byte counters from Linux's /proc/net/dev, leaving out loopback.
func parseNetDev(text string) map[string]netCounters {
	counters := map[string]netCounters{}
	for _, line := range strings.Split(text, "\n") {
		name, rest, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		name = strings.TrimSpace(name)
		fields := strings.Fields(rest)
		if name == "lo" || len(fields) < 9 {
			continue
		}
		rx, err1 := strconv.ParseUint(fields[0], 10, 64)
		tx, err2 := strconv.ParseUint(fields[8], 10, 64)
		if err1 == nil && err2 == nil {
			counters[name] = netCounters{rx: rx, tx: tx}
		}
	}
	return counters
}

// realFilesystems are the Linux filesystem types reported as disks.
var realFilesystems = map[string]bool{
	"ext2": true, "ext3": true, "ext4": true, "xfs": true, "btrfs": true, "zfs": true, "jfs": true,
	"reiserfs": true, "f2fs": true, "vfat": true, "exfat": true, "ntfs": true, "ntfs3": true, "fuseblk": true,
}

// parseMounts lists the mount points of real filesystems in /proc/mounts,
// once per device.
func parseMounts(text string) []string {
	var mounts []string
	seen := map[string]bool{}
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 || !realFilesystems[fields[2]] || seen[fields[0]] {
			continue
		}
		seen[fields[0]] = true
		mounts = append(mounts, strings.ReplaceAll(fields[1], `\040`, " "))
	}
	return mounts
}

// parseVMStat reads macOS vm_stat output: the page size and the free,
// inactive, speculative and purgeable pages that can be handed out.
func parseVMStat(text string) (uint64, error) {
	scanner := bufio.NewScanner(strings.NewReader(text))
	pageSize := uint64(4096)
	var pages uint64
	found := false
	for scanner.Scan() {
		line := scanner.Text()
		if strings.Contains(line, "page size of") {
			for _, field := range strings.Fields(line) {
				if value, err := strconv.ParseUint(field, 10, 64); err == nil {
					pageSize = value
				}
			}
			continue
		}
		name, rest, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		switch strings.TrimSpace(name) {
		case "Pages free", "Pages inactive", "Pages speculative", "Pages purgeable":
			value, err := strconv.ParseUint(strings.TrimSuffix(strings.TrimSpace(rest), "."), 10, 64)
			if err == nil {
				pages += value
				found = true
			}
		}
	}
	if !found {
		return 0, errors.New("no page counts in vm_stat output")
	}
	return pages * pageSize, nil
}

// parseNetstat reads macOS "netstat -ibn" output: each interface's link row
// carries its byte counters. Loopback is left out.
func parseNetstat(text string) map[string]netCounters {
	counters := map[string]netCounters{}
	lines := strings.Split(text, "\n")
	if len(lines) == 0 {
		return counters
	}
	header := strings.Fields(lines[0])
	ibytes, obytes := -1, -1
	for i, name := range header {
		switch name {
		case "Ibytes":
			ibytes = i
		case "Obytes":
			obytes = i
		}
	}
	if ibytes < 0 || obytes < 0 {
		return counters
	}
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		// Link rows have no address column when the interface has no MAC
		// address, so count from the end of the row.
		if len(fields) < 7 || !strings.HasPrefix(fields[2], "<Link#") || strings.HasPrefix(fields[0], "lo") {
			continue
		}
		offset := len(header) - len(fields)
		rx, err1 := strconv.ParseUint(fields[ibytes-offset], 10, 64)
		tx, err2 := strconv.ParseUint(fields[obytes-offset], 10, 64)
		if err1 == nil && err2 == nil {
			counters[fields[0]] = netCounters{rx: rx, tx: tx}
		}
	}
	return counters
}

// parseTopCPU reads the last "CPU usage: ... % idle" line of macOS top.
func parseTopCPU(text string) (float64, bool) {
	value, found := 0.0, false
	for _, line := range strings.Split(text, "\n") {
		if !strings.HasPrefix(strings.TrimSpace(line), "CPU usage:") {
			continue
		}
		for _, part := range strings.Split(line, ",") {
			part = strings.TrimSpace(part)
			if !strings.HasSuffix(part, "idle") {
				continue
			}
			fields := strings.Fields(part)
			idle, err := strconv.ParseFloat(strings.TrimSuffix(fields[0], "%"), 64)
			if err == nil {
				value, found = 100-idle, true
			}
		}
	}
	return value, found
}

// commandName is a program's name from its path: what follows the last "/".
func commandName(path string) string {
	path = strings.TrimSpace(path)
	return path[strings.LastIndex(path, "/")+1:]
}

// parseCmdline reads the program name from Linux's /proc/<pid>/cmdline, whose
// arguments are separated by NULs. Kernel threads have none.
func parseCmdline(data string) string {
	if i := strings.IndexByte(data, 0); i >= 0 {
		data = data[:i]
	}
	return commandName(data)
}

// parsePsComm reads macOS's "ps -axo comm=": each process's program path.
func parsePsComm(text string) [][]string {
	var running [][]string
	for _, line := range strings.Split(text, "\n") {
		if name := commandName(line); name != "" {
			running = append(running, []string{name})
		}
	}
	return running
}
