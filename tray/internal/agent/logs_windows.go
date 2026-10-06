//go:build windows

package agent

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os/exec"
	"strings"
)

// defaultPlatformSources are the event channels read when the server names
// none. Security is included because most endpoint incidents (logon, RDP,
// service changes, tamper alerts) surface there first.
var defaultPlatformSources = []string{"System", "Application", "Security"}

// Log files may be read from these folders, compared case-insensitively.
var (
	platformFileRoots = WindowsLogFileRoots
	platformPathStyle = windowsPaths
)

// isPlatformLogSource reports whether source is an allowlisted channel or
// log file.
func isPlatformLogSource(source string) bool {
	return isWindowsLogSource(source) || isFileLogSource(source)
}

// collectPlatformLogs reads each requested Windows Event Log channel with
// wevtutil, which is built into Windows and read-only. It is used instead of
// PowerShell's Get-WinEvent because PowerShell module auto-loading on a
// fresh SYSTEM profile wrote progress records to stderr and failed the query.
// Only allowlisted channels are read; the channel is passed as its own
// argument, never through a shell.
func collectPlatformLogs(ctx context.Context, specs []LogSpec) (string, []string, error) {
	wevtutil, err := exec.LookPath("wevtutil.exe")
	if err != nil {
		return "", nil, fmt.Errorf("locate wevtutil: %w", err)
	}
	var b strings.Builder
	var sources []string
	for _, spec := range specs {
		if !isWindowsLogSource(spec.Source) {
			fmt.Fprintf(&b, "### %s Log\n[refused: not an allowlisted channel]\n\n", spec.Source)
			continue
		}
		out, err := runWevtutil(ctx, wevtutil, WevtutilQueryArgs(spec))
		if err != nil {
			fmt.Fprintf(&b, "### %s Log\n[unavailable: %s]\n\n", spec.Source, err)
			continue
		}
		text, parseErr := FormatRenderedEvents(out)
		if parseErr != nil && text == "" {
			fmt.Fprintf(&b, "### %s Log\n[unavailable: could not read events: %s]\n\n", spec.Source, parseErr)
			continue
		}
		if text == "" {
			text = "[no entries in this window]"
		}
		fmt.Fprintf(&b, "### %s Log\n%s\n\n", spec.Source, text)
		sources = append(sources, spec.Source)
	}
	return b.String(), sources, nil
}

func runWevtutil(ctx context.Context, wevtutil string, args []string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, wevtutil, args...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("timed out: %w", ctx.Err())
		}
		msg := strings.Join(strings.Fields(stderr.String()), " ")
		if msg == "" {
			msg = strings.Join(strings.Fields(string(out)), " ")
		}
		if len(msg) > 400 {
			msg = msg[:400] + "…"
		}
		return nil, fmt.Errorf("%w: %s", err, msg)
	}
	return out, nil
}
