package checks

import (
	"context"
	"os/exec"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

// PingCount is how many echo requests each ping check sends.
const PingCount = 4

var replyTime = regexp.MustCompile(`[=<]\s*(\d+(?:[.,]\d+)?)\s*ms`)

func pingArgs(goos, target string) []string {
	count := strconv.Itoa(PingCount)
	switch goos {
	case "windows":
		return []string{"-n", count, "-w", "1000", target}
	case "darwin":
		return []string{"-c", count, "-W", "1000", target}
	default:
		return []string{"-c", count, "-W", "1", target}
	}
}

// Ping pings target with the system's ping command, which needs no special
// privileges and works the same for IPv4 and IPv6.
func Ping(ctx context.Context, target string) client.PingResult {
	result := client.PingResult{Target: target, Sent: PingCount}
	if !validName(target) {
		result.Error = "not a host name or address"
		return result
	}
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "ping", pingArgs(runtime.GOOS, target)...)
	hideWindow(cmd)
	output, err := cmd.CombinedOutput()
	result.Received, result.AvgMs = ParsePing(string(output))
	if result.Received == 0 {
		message := strings.TrimSpace(lastLine(string(output)))
		if message == "" && err != nil {
			message = err.Error()
		}
		result.Error = message
	}
	return result
}

// ParsePing counts the replies in ping's output and averages their times.
// Every reply line carries a TTL whatever the system's language.
func ParsePing(output string) (int, *float64) {
	received := 0
	total, timed := 0.0, 0
	for _, line := range strings.Split(output, "\n") {
		if !strings.Contains(strings.ToLower(line), "ttl=") {
			continue
		}
		received++
		if match := replyTime.FindStringSubmatch(line); match != nil {
			if value, err := strconv.ParseFloat(strings.Replace(match[1], ",", ".", 1), 64); err == nil {
				total += value
				timed++
			}
		}
	}
	if received > PingCount {
		received = PingCount
	}
	if timed == 0 {
		return received, nil
	}
	average := total / float64(timed)
	return received, &average
}

func lastLine(text string) string {
	lines := strings.Split(strings.TrimSpace(text), "\n")
	if len(lines) == 0 {
		return ""
	}
	line := strings.TrimSpace(lines[len(lines)-1])
	if len(line) > 300 {
		line = line[:300]
	}
	return line
}
