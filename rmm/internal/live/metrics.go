package live

import (
	"context"
	"time"
)

// Metrics is one real-time CPU and memory sample.
type Metrics struct {
	Time          time.Time `json:"time"`
	CPUPercent    float64   `json:"cpu_percent"`
	Cores         []float64 `json:"cores"`
	MemoryTotal   uint64    `json:"memory_total"`
	MemoryUsed    uint64    `json:"memory_used"`
	SwapTotal     uint64    `json:"swap_total"`
	SwapUsed      uint64    `json:"swap_used"`
	Processes     int       `json:"processes"`
	UptimeSeconds uint64    `json:"uptime_seconds"`
	NetRxBps      float64   `json:"net_rx_bps"`
	NetTxBps      float64   `json:"net_tx_bps"`
	Errors        []string  `json:"errors,omitempty"`
}

type memoryInfo struct {
	total, used, swapTotal, swapUsed uint64
}

// cpuTimes are cumulative busy and total ticks; index 0 is the whole
// machine and the rest are its logical processors.
type cpuTimes struct{ busy, total uint64 }

func cpuPercent(before, after cpuTimes) float64 {
	if after.total <= before.total || after.busy < before.busy {
		return 0
	}
	percent := float64(after.busy-before.busy) * 100 / float64(after.total-before.total)
	if percent > 100 {
		return 100
	}
	return percent
}

type metricsSampler struct {
	cpu     []cpuTimes
	rx, tx  uint64
	netAt   time.Time
	haveNet bool
}

func (m *metricsSampler) sample() Metrics {
	now := time.Now()
	out := Metrics{Time: now.UTC(), Cores: []float64{}}
	if cpu, err := readCPUTimes(); err != nil {
		out.Errors = append(out.Errors, "cpu: "+err.Error())
	} else if len(cpu) > 0 {
		if len(m.cpu) == len(cpu) {
			out.CPUPercent = cpuPercent(m.cpu[0], cpu[0])
			for i := 1; i < len(cpu); i++ {
				out.Cores = append(out.Cores, cpuPercent(m.cpu[i], cpu[i]))
			}
		}
		m.cpu = cpu
	} else if percent, ok := directCPUPercent(); ok {
		out.CPUPercent = percent
	}
	if mem, err := readMemoryInfo(); err != nil {
		out.Errors = append(out.Errors, "memory: "+err.Error())
	} else {
		out.MemoryTotal, out.MemoryUsed = mem.total, mem.used
		out.SwapTotal, out.SwapUsed = mem.swapTotal, mem.swapUsed
	}
	if rx, tx, err := readNetTotals(); err == nil {
		if m.haveNet && rx >= m.rx && tx >= m.tx {
			elapsed := now.Sub(m.netAt).Seconds()
			if elapsed > 0 {
				out.NetRxBps = float64(rx-m.rx) / elapsed
				out.NetTxBps = float64(tx-m.tx) / elapsed
			}
		}
		m.rx, m.tx, m.netAt, m.haveNet = rx, tx, now, true
	}
	out.Processes = processCount()
	out.UptimeSeconds = uptimeSeconds()
	return out
}

type metricsArgs struct {
	IntervalMS int `json:"interval_ms"`
}

func init() {
	register("metrics.start", func(s *Session, req Request) (interface{}, error) {
		var args metricsArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		interval := time.Duration(args.IntervalMS) * time.Millisecond
		if interval < 500*time.Millisecond {
			interval = time.Second
		}
		if interval > 10*time.Second {
			interval = 10 * time.Second
		}
		ctx, cancel := context.WithCancel(s.ctx)
		s.mu.Lock()
		if s.metrics != nil {
			s.metrics()
		}
		s.metrics = cancel
		s.mu.Unlock()
		go func() {
			sampler := &metricsSampler{}
			sampler.sample()
			ticker := time.NewTicker(interval)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-ticker.C:
					if !s.event("metrics", sampler.sample()) {
						return
					}
				}
			}
		}()
		return map[string]int64{"interval_ms": interval.Milliseconds()}, nil
	})
	register("metrics.stop", func(s *Session, req Request) (interface{}, error) {
		s.mu.Lock()
		if s.metrics != nil {
			s.metrics()
			s.metrics = nil
		}
		s.mu.Unlock()
		return nil, nil
	})
}
