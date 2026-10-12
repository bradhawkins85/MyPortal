//go:build windows

package inventory

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/base64"
	"fmt"
	"os/exec"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
)

//go:embed windows.ps1
var windowsScript string

func collectPlatform(ctx context.Context, s *Snapshot) {
	s.OSName = "Windows"
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	// -EncodedCommand takes UTF-16LE base64, which avoids quoting and the
	// execution policy applying to a script file.
	units := utf16.Encode([]rune(windowsScript))
	raw := make([]byte, len(units)*2)
	for i, unit := range units {
		raw[i*2] = byte(unit)
		raw[i*2+1] = byte(unit >> 8)
	}
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
		"-EncodedCommand", base64.StdEncoding.EncodeToString(raw))
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		detail := strings.TrimSpace(stderr.String())
		if len(detail) > 500 {
			detail = detail[:500]
		}
		s.fail("powershell", fmt.Errorf("%v %s", err, detail))
		return
	}
	s.fail("powershell", applyWindows(s, bytes.TrimPrefix(stdout.Bytes(), []byte("\xef\xbb\xbf"))))
}
