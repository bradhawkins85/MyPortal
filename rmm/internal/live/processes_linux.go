package live

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Linux reports process times in clock ticks; USER_HZ is 100 on every
// supported architecture.
const clockTicks = 100

func bootTime() time.Time {
	data, err := os.ReadFile("/proc/stat")
	if err != nil {
		return time.Time{}
	}
	for _, line := range strings.Split(string(data), "\n") {
		if value, ok := strings.CutPrefix(line, "btime "); ok {
			if sec, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64); err == nil {
				return time.Unix(sec, 0)
			}
		}
	}
	return time.Time{}
}

func readProcesses() ([]Process, error) {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return nil, err
	}
	boot := bootTime()
	pageSize := uint64(os.Getpagesize())
	var procs []Process
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || !entry.IsDir() {
			continue
		}
		dir := filepath.Join("/proc", entry.Name())
		stat, err := os.ReadFile(filepath.Join(dir, "stat"))
		if err != nil {
			continue
		}
		proc, err := parseProcPIDStat(string(stat), pageSize, boot)
		if err != nil {
			continue
		}
		proc.PID = pid
		if status, err := os.ReadFile(filepath.Join(dir, "status")); err == nil {
			if uid := statusUID(string(status)); uid != "" {
				proc.User = userName(uid)
			}
		}
		if cmdline, err := os.ReadFile(filepath.Join(dir, "cmdline")); err == nil {
			proc.Command = cmdlineText(cmdline)
		}
		if exe, err := os.Readlink(filepath.Join(dir, "exe")); err == nil {
			proc.Path = exe
		}
		procs = append(procs, proc)
	}
	if len(procs) == 0 {
		return nil, errors.New("no processes could be read from /proc")
	}
	return procs, nil
}

// parseProcPIDStat reads /proc/<pid>/stat. The name is in parentheses and
// may itself hold spaces or parentheses, so fields are counted from the last
// closing parenthesis.
func parseProcPIDStat(text string, pageSize uint64, boot time.Time) (Process, error) {
	open := strings.IndexByte(text, '(')
	closing := strings.LastIndexByte(text, ')')
	if open < 0 || closing < open {
		return Process{}, errors.New("malformed stat")
	}
	fields := strings.Fields(text[closing+1:])
	if len(fields) < 22 {
		return Process{}, errors.New("short stat")
	}
	num := func(i int) uint64 {
		value, _ := strconv.ParseUint(fields[i], 10, 64)
		return value
	}
	proc := Process{
		Name:        text[open+1 : closing],
		PPID:        int(num(1)),
		Threads:     int(num(17)),
		MemoryBytes: num(21) * pageSize,
		cpuTime:     time.Duration(num(11)+num(12)) * time.Second / clockTicks,
	}
	if !boot.IsZero() {
		started := boot.Add(time.Duration(num(19)) * time.Second / clockTicks).UTC()
		proc.Started = &started
	}
	return proc, nil
}

func statusUID(text string) string {
	for _, line := range strings.Split(text, "\n") {
		if value, ok := strings.CutPrefix(line, "Uid:"); ok {
			if fields := strings.Fields(value); len(fields) > 0 {
				return fields[0]
			}
		}
	}
	return ""
}

func cmdlineText(raw []byte) string {
	text := string(bytes.TrimRight(bytes.ReplaceAll(raw, []byte{0}, []byte{' '}), " "))
	if len(text) > 2048 {
		text = text[:2048]
	}
	return text
}
