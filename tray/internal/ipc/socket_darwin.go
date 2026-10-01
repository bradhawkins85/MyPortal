//go:build darwin

package ipc

import (
	"fmt"
	"os"
	"os/user"
	"strconv"
)

// configureSocket lets the per-user LaunchAgent connect to the root-owned
// LaunchDaemon without running the UI as root. All interactive macOS users are
// members of staff; keeping the socket group-only prevents other local service
// accounts from issuing IPC commands.
func configureSocket(path string) error {
	group, err := user.LookupGroup("staff")
	if err != nil {
		return fmt.Errorf("ipc: look up staff group: %w", err)
	}
	gid, err := strconv.Atoi(group.Gid)
	if err != nil {
		return fmt.Errorf("ipc: invalid staff group id %q: %w", group.Gid, err)
	}
	if err := os.Chown(path, 0, gid); err != nil {
		return fmt.Errorf("ipc: set socket owner: %w", err)
	}
	if err := os.Chmod(path, 0660); err != nil {
		return fmt.Errorf("ipc: set socket permissions: %w", err)
	}
	return nil
}
