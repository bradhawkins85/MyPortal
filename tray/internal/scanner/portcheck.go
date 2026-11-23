package scanner

import (
	"context"
	"net"
	"sort"
	"strconv"
	"sync"
	"time"
)

// MaxPortCheckPorts caps one port check, matching the portal's limit.
const MaxPortCheckPorts = 1024

// IPInTargets reports whether address is inside one of the scan targets.
// Unlike IPAllowed, an empty target list allows nothing.
func IPInTargets(address string, targets []string) bool {
	ip := net.ParseIP(address)
	if ip == nil {
		return false
	}
	for _, raw := range targets {
		_, network, err := net.ParseCIDR(raw)
		if err == nil && network.Contains(ip) {
			return true
		}
	}
	return false
}

// CheckPorts tries a TCP connection to each port on ip and returns the sorted
// ports that accepted one. Closed and filtered ports are not distinguished.
func CheckPorts(ctx context.Context, ip string, ports []int, timeout time.Duration, workers int) []int {
	if workers < 1 {
		workers = 1
	}
	jobs := make(chan int)
	var (
		mu   sync.Mutex
		open []int
		wg   sync.WaitGroup
	)
	dialer := net.Dialer{Timeout: timeout}
	for i := 0; i < workers; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for port := range jobs {
				conn, err := dialer.DialContext(ctx, "tcp", net.JoinHostPort(ip, strconv.Itoa(port)))
				if err != nil {
					continue
				}
				conn.Close()
				mu.Lock()
				open = append(open, port)
				mu.Unlock()
			}
		}()
	}
feed:
	for _, port := range ports {
		select {
		case <-ctx.Done():
			break feed
		case jobs <- port:
		}
	}
	close(jobs)
	wg.Wait()
	sort.Ints(open)
	return open
}
