//go:build windows

package thermal

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"

	"github.com/bradhawkins85/myportal-rmm/internal/state"
	"golang.org/x/sys/windows/registry"
)

func pawnIOUninstallCommand() (string, error) {
	// Official installations can register in either registry view.
	for _, view := range []uint32{registry.WOW64_64KEY, registry.WOW64_32KEY} {
		key, err := registry.OpenKey(registry.LOCAL_MACHINE,
			`SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO`, registry.QUERY_VALUE|view)
		if errors.Is(err, registry.ErrNotExist) {
			continue
		}
		if err != nil {
			return "", err
		}
		for _, name := range []string{"QuietUninstallString", "UninstallString"} {
			command, _, err := key.GetStringValue(name)
			if err == nil && command != "" {
				key.Close()
				return command, nil
			}
			if err != nil && !errors.Is(err, registry.ErrNotExist) {
				key.Close()
				return "", err
			}
		}
		key.Close()
		return "", errors.New("PawnIO is registered without an uninstall command")
	}
	return "", nil
}

func runUninstaller(ctx context.Context, program, args string) error {
	cmd := exec.CommandContext(ctx, program)
	// Pass the registered arguments through unchanged, as Windows would.
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CmdLine: syscall.EscapeArg(program) + " " + args}
	return cmd.Run()
}

func preparePlatform(ctx context.Context) error {
	// Earlier agents extracted LibreHardwareMonitor here; it is no longer used.
	_ = os.RemoveAll(filepath.Join(state.Dir(), "LibreHardwareMonitor"))
	return removePawnIO(ctx, pawnIOUninstallCommand, runUninstaller)
}
