package live

import (
	"errors"
	"fmt"
	"os"
	"runtime"
	"sort"
	"sync"
	"time"
)

// Process is one row of the task manager.
type Process struct {
	PID         int        `json:"pid"`
	PPID        int        `json:"ppid"`
	Name        string     `json:"name"`
	User        string     `json:"user"`
	CPUPercent  float64    `json:"cpu_percent"`
	MemoryBytes uint64     `json:"memory_bytes"`
	Threads     int        `json:"threads"`
	Path        string     `json:"path,omitempty"`
	Command     string     `json:"command,omitempty"`
	Started     *time.Time `json:"started,omitempty"`

	// cpuTime is cumulative CPU time; platforms that report a share
	// directly set CPUPercent and leave it at -1.
	cpuTime time.Duration
}

const maxProcesses = 5000

// procSampler keeps the previous CPU times so each list shows recent use.
type procSampler struct {
	mu   sync.Mutex
	at   time.Time
	cpu  map[int]time.Duration
	freq time.Duration
}

func newProcSampler() *procSampler { return &procSampler{freq: 500 * time.Millisecond} }

func (p *procSampler) list() ([]Process, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.cpu == nil || time.Since(p.at) > 30*time.Second {
		first, err := readProcesses()
		if err != nil {
			return nil, err
		}
		p.remember(first)
		time.Sleep(p.freq)
	}
	procs, err := readProcesses()
	if err != nil {
		return nil, err
	}
	elapsed := time.Since(p.at)
	cores := float64(runtime.NumCPU())
	for i := range procs {
		if procs[i].cpuTime < 0 {
			procs[i].CPUPercent = min(procs[i].CPUPercent/cores, 100)
			continue
		}
		before, ok := p.cpu[procs[i].PID]
		if ok && procs[i].cpuTime >= before && elapsed > 0 {
			percent := float64(procs[i].cpuTime-before) * 100 / float64(elapsed) / cores
			procs[i].CPUPercent = min(percent, 100)
		}
	}
	p.remember(procs)
	sort.Slice(procs, func(i, j int) bool { return procs[i].PID < procs[j].PID })
	if len(procs) > maxProcesses {
		procs = procs[:maxProcesses]
	}
	return procs, nil
}

func (p *procSampler) remember(procs []Process) {
	p.cpu = make(map[int]time.Duration, len(procs))
	for _, proc := range procs {
		p.cpu[proc.PID] = proc.cpuTime
	}
	p.at = time.Now()
}

// descendants lists pid's child processes, deepest first, so a tree is
// ended from the leaves up.
func descendants(procs []Process, pid int) []int {
	children := map[int][]int{}
	for _, proc := range procs {
		if proc.PID != proc.PPID {
			children[proc.PPID] = append(children[proc.PPID], proc.PID)
		}
	}
	var out []int
	seen := map[int]bool{pid: true}
	var walk func(int)
	walk = func(parent int) {
		for _, child := range children[parent] {
			if seen[child] {
				continue
			}
			seen[child] = true
			walk(child)
			out = append(out, child)
		}
	}
	walk(pid)
	return out
}

func protectedPID(pid int) error {
	if pid <= 0 || pid == os.Getpid() || protectedSystemPID(pid) {
		return fmt.Errorf("process %d cannot be ended from here", pid)
	}
	return nil
}

type killArgs struct {
	PID   int  `json:"pid"`
	Force bool `json:"force"`
	Tree  bool `json:"tree"`
}

func init() {
	register("process.list", func(s *Session, req Request) (interface{}, error) {
		procs, err := s.procs.list()
		if err != nil {
			return nil, err
		}
		return map[string]interface{}{"processes": procs, "cores": runtime.NumCPU()}, nil
	})
	register("process.kill", func(s *Session, req Request) (interface{}, error) {
		var args killArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		if err := protectedPID(args.PID); err != nil {
			return nil, err
		}
		targets := []int{}
		if args.Tree {
			procs, err := readProcesses()
			if err != nil {
				return nil, err
			}
			for _, child := range descendants(procs, args.PID) {
				if protectedPID(child) == nil {
					targets = append(targets, child)
				}
			}
		}
		targets = append(targets, args.PID)
		var errs []error
		ended := 0
		for _, pid := range targets {
			if err := killProcess(pid, args.Force); err != nil {
				errs = append(errs, fmt.Errorf("process %d: %w", pid, err))
				continue
			}
			ended++
		}
		if ended == 0 && len(errs) > 0 {
			return nil, errors.Join(errs...)
		}
		result := map[string]interface{}{"ended": ended}
		if len(errs) > 0 {
			result["warning"] = errors.Join(errs...).Error()
		}
		return result, nil
	})
}
