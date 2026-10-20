// Package checks measures what MyPortal's checks watch: CPU load, memory,
// disk space, network throughput, ping targets, services, files and
// processes. MyPortal says what to measure (client.CheckConfig) and judges the
// results; a service check may also have the agent start, restart or stop the
// service.
package checks

import (
	"context"
	"strings"
	"sync"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

// Collector keeps the previous CPU and network counters so each report
// covers the time since the last one.
type Collector struct {
	cpu     cpuSampler
	netPrev map[string]netCounters
	netTime time.Time
}

type netCounters struct {
	rx, tx uint64
}

// NewCollector returns a collector; call Prime once before the first Collect.
func NewCollector() *Collector {
	return &Collector{cpu: newCPUSampler()}
}

// Prime takes the first CPU and network readings so the first report has
// averages over a whole interval.
func (c *Collector) Prime() {
	c.cpu.Percent()
	c.network()
}

// Collect measures what cfg asks for.
func (c *Collector) Collect(ctx context.Context, cfg client.CheckConfig) client.CheckReport {
	var report client.CheckReport
	if cfg.CPU {
		if percent, ok := c.cpu.Percent(); ok {
			report.CPUPercent = &percent
		}
	}
	// Always read the counters so the next average starts from now.
	rates := c.network()
	if cfg.Network {
		report.Network = rates
	}
	if cfg.Memory {
		if total, available, err := readMemory(); err == nil && total > 0 {
			report.Memory = &client.MemoryUsage{TotalBytes: total, AvailableBytes: available}
		}
	}
	if cfg.Disks {
		if disks, err := readDisks(); err == nil {
			report.Disks = disks
			if report.Disks == nil {
				report.Disks = []client.DiskUsage{}
			}
		}
	}
	var wg sync.WaitGroup
	report.Pings = make([]client.PingResult, len(cfg.Pings))
	for i, target := range cfg.Pings {
		wg.Add(1)
		go func(i int, target string) {
			defer wg.Done()
			report.Pings[i] = Ping(ctx, target)
		}(i, target)
	}
	for _, svc := range cfg.Services {
		report.Services = append(report.Services, CheckService(ctx, svc))
	}
	for _, path := range cfg.Files {
		report.Files = append(report.Files, CheckFile(path))
	}
	if len(cfg.Processes) > 0 {
		report.Processes = CheckProcesses(cfg.Processes)
	}
	wg.Wait()
	return report
}

// network returns each interface's throughput since the last call, or nil
// the first time.
func (c *Collector) network() []client.InterfaceRate {
	counters, err := readNetwork()
	now := time.Now()
	if err != nil {
		return nil
	}
	previous, since := c.netPrev, c.netTime
	c.netPrev, c.netTime = counters, now
	if previous == nil {
		return nil
	}
	return Rates(previous, counters, now.Sub(since))
}

// Rates turns two readings of interface byte counters into bytes a second.
// An interface whose counters went backwards (reset or wrapped) is left out.
func Rates(previous, current map[string]netCounters, elapsed time.Duration) []client.InterfaceRate {
	seconds := elapsed.Seconds()
	rates := []client.InterfaceRate{}
	if seconds <= 0 {
		return rates
	}
	for name, now := range current {
		before, ok := previous[name]
		if !ok || now.rx < before.rx || now.tx < before.tx {
			continue
		}
		rates = append(rates, client.InterfaceRate{
			Name:  name,
			RxBps: float64(now.rx-before.rx) / seconds,
			TxBps: float64(now.tx-before.tx) / seconds,
		})
	}
	return rates
}

// cpuTimes are cumulative CPU times in any unit.
type cpuTimes struct {
	idle, total uint64
}

// busyPercent is how busy the CPU was between two readings.
func busyPercent(before, after cpuTimes) (float64, bool) {
	if after.total <= before.total || after.idle < before.idle {
		return 0, false
	}
	total := float64(after.total - before.total)
	idle := float64(after.idle - before.idle)
	percent := 100 * (total - idle) / total
	if percent < 0 {
		percent = 0
	}
	if percent > 100 {
		percent = 100
	}
	return percent, true
}

// timesSampler works out CPU load from cumulative CPU times.
type timesSampler struct {
	read func() (cpuTimes, error)
	last *cpuTimes
}

func (s *timesSampler) Percent() (float64, bool) {
	now, err := s.read()
	if err != nil {
		return 0, false
	}
	previous := s.last
	s.last = &now
	if previous == nil {
		return 0, false
	}
	return busyPercent(*previous, now)
}

type cpuSampler interface {
	Percent() (float64, bool)
}

// validName rejects names that could be read as command-line options.
func validName(name string) bool {
	name = strings.TrimSpace(name)
	return name != "" && !strings.HasPrefix(name, "-") && !strings.ContainsAny(name, "\x00\r\n")
}
