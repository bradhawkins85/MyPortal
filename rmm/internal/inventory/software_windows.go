//go:build windows

package inventory

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"golang.org/x/sys/windows/registry"
)

// InstalledPrograms reads both registry views and loaded user hives. It avoids
// Win32_Product, which can trigger MSI repair operations when queried.
func InstalledPrograms(ctx context.Context) ([]Program, error) {
	const uninstall = `SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall`
	var programs []Program
	read := func(root registry.Key, path string, view uint32) error {
		key, err := registry.OpenKey(root, path, registry.READ|view)
		if errors.Is(err, registry.ErrNotExist) {
			return nil
		}
		if err != nil {
			return err
		}
		defer key.Close()
		names, err := key.ReadSubKeyNames(-1)
		if err != nil {
			return err
		}
		for _, name := range names {
			if err := ctx.Err(); err != nil {
				return err
			}
			item, err := registry.OpenKey(key, name, registry.QUERY_VALUE|view)
			if errors.Is(err, registry.ErrNotExist) {
				continue
			}
			if err != nil {
				return err
			}
			display, _, _ := item.GetStringValue("DisplayName")
			version, _, _ := item.GetStringValue("DisplayVersion")
			component, _, _ := item.GetIntegerValue("SystemComponent")
			parent, _, _ := item.GetStringValue("ParentKeyName")
			release, _, _ := item.GetStringValue("ReleaseType")
			item.Close()
			if strings.TrimSpace(display) != "" && component != 1 && parent == "" && release == "" {
				programs = append(programs, Program{Name: display, Version: version})
			}
		}
		return nil
	}
	for _, view := range []uint32{registry.WOW64_64KEY, registry.WOW64_32KEY} {
		if err := read(registry.LOCAL_MACHINE, uninstall, view); err != nil {
			return nil, fmt.Errorf("could not list installed programs: %w", err)
		}
	}
	users, err := registry.USERS.ReadSubKeyNames(-1)
	if err != nil {
		return nil, fmt.Errorf("could not list user software: %w", err)
	}
	for _, user := range users {
		if !strings.HasPrefix(user, "S-1-5-21-") || strings.HasSuffix(user, "_Classes") {
			continue
		}
		if err := read(registry.USERS, user+`\`+uninstall, 0); err != nil {
			return nil, fmt.Errorf("could not list user software: %w", err)
		}
	}
	return programs, nil
}
