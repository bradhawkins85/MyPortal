//go:build windows

package main

import (
	"fmt"
	"path/filepath"

	"github.com/bradhawkins85/myportal-tray/internal/secdir"
)

// secureStateDirs gives %ProgramData%\MyPortal and its tray folder a
// protected access list (SYSTEM and Administrators: full control; Users:
// read only) and removes anything in them the service did not create.
//
// Without it the folders inherit ProgramData's permissions, which let any
// local user create tray-state.json or tray-config.json before the service
// does and keep control of them, redirecting the UI and the overseer to
// another server or planting menu links. The per-user tray UI still needs to
// read both files, so Users keep read access.
//
// It returns messages to log once the logger is ready.
func secureStateDirs() []string {
	var notes []string
	dir := stateDir()
	for _, p := range []string{filepath.Dir(dir), dir} {
		if err := secdir.Secure(p, secdir.UsersRead); err != nil {
			notes = append(notes, fmt.Sprintf("Could not secure %s: %v", p, err))
		}
	}
	for _, p := range secdir.Sweep(dir) {
		notes = append(notes, fmt.Sprintf("Removed %s from the tray folder: it was a link or was not created by the service", p))
	}
	return notes
}
