//go:build windows

package thermal

import (
	"context"
	"errors"
	"os/exec"
	"path/filepath"
	"syscall"

	"golang.org/x/sys/windows/registry"
)

func installedPawnIOVersion() (string, error) {
	installed := ""
	// Official installations can register in either registry view. Preserve
	// a compatible installation regardless of the agent's own architecture.
	for _, view := range []uint32{registry.WOW64_64KEY, registry.WOW64_32KEY} {
		key, err := registry.OpenKey(registry.LOCAL_MACHINE,
			`SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO`, registry.QUERY_VALUE|view)
		if errors.Is(err, registry.ErrNotExist) {
			continue
		}
		if err != nil {
			return "", err
		}
		version, _, err := key.GetStringValue("DisplayVersion")
		key.Close()
		if errors.Is(err, registry.ErrNotExist) {
			continue
		}
		if err != nil {
			return "", err
		}
		if compatiblePawnIO(version) {
			return version, nil
		}
		installed = version
	}
	return installed, nil
}

func windowsMachineIdentity() (string, string, error) {
	key, err := registry.OpenKey(registry.LOCAL_MACHINE,
		`HARDWARE\DESCRIPTION\System\BIOS`, registry.QUERY_VALUE)
	if err != nil {
		return "", "", err
	}
	defer key.Close()
	manufacturer, _, err := key.GetStringValue("SystemManufacturer")
	if err != nil {
		return "", "", err
	}
	model, _, err := key.GetStringValue("SystemProductName")
	return manufacturer, model, err
}

func preparePlatform(ctx context.Context) error {
	return preparePhysicalMachine(ctx, windowsMachineIdentity, installPhysicalPawnIO)
}

func installPhysicalPawnIO(ctx context.Context) error {
	return preparePawnIO(ctx, installedPawnIOVersion, func(ctx context.Context) error {
		// Verify all extracted files against the embedded bundle immediately
		// before executing the pinned installer. No endpoint download is needed.
		dll, err := bundlePath()
		if err != nil {
			return err
		}
		cmd := exec.CommandContext(ctx, filepath.Join(filepath.Dir(dll), "PawnIO_setup.exe"), "-install", "-silent")
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
		return cmd.Run()
	})
}
