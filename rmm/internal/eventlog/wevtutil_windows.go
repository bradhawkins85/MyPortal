//go:build windows

package eventlog

import (
	"bytes"
	"context"
	"fmt"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

// Supported is true where Windows event logs can be read.
const Supported = true

// Wevtutil reads event logs with Windows' wevtutil.exe, which renders each
// event's message the way Event Viewer shows it.
type Wevtutil struct{}

// NewSource returns the Windows event log reader.
func NewSource() Source { return Wevtutil{} }

func (Wevtutil) run(ctx context.Context, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	cmd := exec.CommandContext(ctx, "wevtutil.exe", args...)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		detail := strings.TrimSpace(DecodeOutput(stderr.Bytes()))
		if detail == "" {
			detail = strings.TrimSpace(DecodeOutput(stdout.Bytes()))
		}
		return "", fmt.Errorf("wevtutil %s: %w: %s", args[1], err, detail)
	}
	return DecodeOutput(stdout.Bytes()), nil
}

// Read implements Source.
func (w Wevtutil) Read(ctx context.Context, log string, after uint64, since time.Time, max int) ([]client.EventLogEntry, uint64, int, error) {
	output, err := w.run(ctx, "qe", log,
		"/q:"+Query(after, since, time.Now()),
		"/f:RenderedXml", "/e:Events", "/uni:true",
		"/c:"+strconv.Itoa(max))
	if err != nil {
		return nil, 0, 0, err
	}
	entries, highest, count := Parse(output, log)
	return entries, highest, count, nil
}

// Newest implements Source.
func (w Wevtutil) Newest(ctx context.Context, log string) (uint64, error) {
	output, err := w.run(ctx, "qe", log, "/rd:true", "/c:1", "/f:xml", "/uni:true")
	if err != nil {
		return 0, err
	}
	return NewestRecordID(output), nil
}
