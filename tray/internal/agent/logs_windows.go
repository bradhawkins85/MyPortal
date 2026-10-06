//go:build windows

package agent

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"os/exec"
	"strings"
	"time"
	"unicode/utf16"
)

// defaultPlatformSources are the event channels read when the server names
// none. Security is included because most endpoint incidents (logon, RDP,
// service changes, tamper alerts) surface there first.
var defaultPlatformSources = []string{"System", "Application", "Security"}

// isPlatformLogSource reports whether source is an allowlisted channel.
func isPlatformLogSource(source string) bool { return isWindowsLogSource(source) }

// maxWinEvents caps the number of entries read per channel so a busy machine
// cannot blow past the prompt budget.
const maxWinEvents = 500

// logQueryScript emits one bounded line per event so the prompt stays
// greppable. It is read-only (Get-WinEvent never mutates the logs).
const logQueryScript = `$ErrorActionPreference = 'Stop'
Get-WinEvent -LogName '%s' -Since ([datetime] '%s') -MaxEvents %d -ErrorAction SilentlyContinue |
  ForEach-Object {
    $msg = if ($null -ne $_.Message) { ($_.Message -replace "\r?\n", ' ') } else { '' }
    '{0:yyyy-MM-dd HH:mm:ss} [{1}] id={2}: {3}' -f $_.TimeCreated, $_.LevelDisplayName, $_.Id, $msg.Substring(0, [Math]::Min(400, $msg.Length))
  }`

// collectPlatformLogs reads each requested Windows Event Log channel as plain
// text via PowerShell's Get-WinEvent. It is read-only. Only allowlisted
// channels are read: the name is embedded in the script, so an unlisted name
// is refused rather than quoted.
func collectPlatformLogs(ctx context.Context, specs []LogSpec) (string, []string, error) {
	var b strings.Builder
	var sources []string
	for _, spec := range specs {
		if !isWindowsLogSource(spec.Source) {
			b.WriteString(fmt.Sprintf("### %s Log\n[refused: not an allowlisted channel]\n\n", spec.Source))
			continue
		}
		since := time.Now().Add(-spec.Window).Format("2006-01-02T15:04:05")
		script := fmt.Sprintf(logQueryScript, spec.Source, since, maxWinEvents)
		out, err := runReadOnlyPowerShell(ctx, script)
		if err != nil {
			b.WriteString(fmt.Sprintf("### %s Log\n[unavailable: %s]\n\n", spec.Source, err))
			continue
		}
		text := strings.TrimSpace(string(out))
		if text == "" {
			text = "[no entries in this window]"
		}
		b.WriteString(fmt.Sprintf("### %s Log\n%s\n\n", spec.Source, text))
		sources = append(sources, spec.Source)
	}
	return b.String(), sources, nil
}

// runReadOnlyPowerShell executes script and returns its standard output. The
// script is passed with -EncodedCommand so no argument quoting is involved.
// This mirrors internal/defender's runner and is duplicated here to keep the
// agent package self-contained.
func runReadOnlyPowerShell(ctx context.Context, script string) ([]byte, error) {
	powershell, err := exec.LookPath("powershell.exe")
	if err != nil {
		return nil, fmt.Errorf("locate PowerShell: %w", err)
	}
	cmd := exec.CommandContext(ctx, powershell,
		"-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
		"-EncodedCommand", encodePowerShell(script))
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		if errors.Is(ctx.Err(), context.DeadlineExceeded) {
			return nil, fmt.Errorf("timed out: %w", ctx.Err())
		}
		return nil, fmt.Errorf("%w: %s", err, strings.TrimSpace(stderr.String()))
	}
	return out, nil
}

func encodePowerShell(script string) string {
	encoded := utf16.Encode([]rune(script))
	buf := make([]byte, len(encoded)*2)
	for i, value := range encoded {
		buf[i*2] = byte(value)
		buf[i*2+1] = byte(value >> 8)
	}
	return base64.StdEncoding.EncodeToString(buf)
}
