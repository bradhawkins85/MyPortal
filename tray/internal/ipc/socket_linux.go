//go:build linux

package ipc

import (
	"fmt"
	"os"
)

// configureSocket lets the tray UI, which runs as each signed-in desktop
// user, connect to the root service. Linux has no group every desktop user
// belongs to, so the socket is open to local users; the service only accepts
// harmless requests over it (refresh the menu, scan the network now).
func configureSocket(path string) error {
	if err := os.Chmod(path, 0666); err != nil {
		return fmt.Errorf("ipc: set socket permissions: %w", err)
	}
	return nil
}
