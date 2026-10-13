//go:build windows

package thermal

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/base64"
	"encoding/json"
	"os"
	"os/exec"
	"syscall"
	"unicode/utf16"
)

//go:embed windows.ps1
var windowsScript string

// limitedOutput prevents an unexpected library response consuming unbounded RAM.
type limitedOutput struct{ bytes.Buffer }

func (b *limitedOutput) Write(p []byte) (int, error) {
	n := len(p)
	if remaining := (1 << 20) - b.Len(); remaining > 0 {
		if len(p) > remaining {
			p = p[:remaining]
		}
		_, _ = b.Buffer.Write(p)
	}
	return n, nil
}

func collectPlatform(ctx context.Context, s *Snapshot) {
	dll, err := bundlePath()
	if err != nil {
		s.Status = "error"
		return
	}
	units := utf16.Encode([]rune(windowsScript))
	raw := make([]byte, len(units)*2)
	for i, unit := range units {
		raw[i*2] = byte(unit)
		raw[i*2+1] = byte(unit >> 8)
	}
	cmd := exec.CommandContext(ctx, "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.StdEncoding.EncodeToString(raw))
	cmd.Env = append(os.Environ(), "MYPORTAL_THERMAL_DLL="+dll)
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
	var out limitedOutput
	cmd.Stdout = &out
	if err := cmd.Run(); err != nil {
		s.Status = "error"
		return
	}
	if err := json.Unmarshal(bytes.TrimPrefix(out.Bytes(), []byte("\xef\xbb\xbf")), &s.Sensors); err != nil {
		s.Sensors = []Sensor{}
		s.Status = "error"
	}
}
