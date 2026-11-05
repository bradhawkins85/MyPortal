package live

import (
	"errors"
	"path/filepath"
	"strconv"
	"strings"
)

// readProcesses uses ps, which reports each process's recent CPU share
// directly (as a percentage of one processor).
func readProcesses() ([]Process, error) {
	out, err := command("/bin/ps", "-axww", "-o", "pid=,ppid=,user=,%cpu=,rss=,comm=")
	if err != nil {
		return nil, err
	}
	procs := parsePsProcesses(out)
	if len(procs) == 0 {
		return nil, errors.New("ps listed no processes")
	}
	return procs, nil
}

func parsePsProcesses(text string) []Process {
	var procs []Process
	for _, line := range strings.Split(text, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 6 {
			continue
		}
		pid, err1 := strconv.Atoi(fields[0])
		ppid, err2 := strconv.Atoi(fields[1])
		cpu, err3 := strconv.ParseFloat(strings.ReplaceAll(fields[3], ",", "."), 64)
		rss, err4 := strconv.ParseUint(fields[4], 10, 64)
		if err1 != nil || err2 != nil || err3 != nil || err4 != nil {
			continue
		}
		// comm is last and may contain spaces.
		path := strings.Join(fields[5:], " ")
		procs = append(procs, Process{
			PID: pid, PPID: ppid, User: fields[2], CPUPercent: cpu, MemoryBytes: rss * 1024,
			Name: filepath.Base(path), Path: path, cpuTime: -1,
		})
	}
	return procs
}
