//go:build linux || darwin

package inventory

import (
	"context"
	"os/exec"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

// usage returns a mount point's size and the space free to users.
func usage(path string) (size, free uint64, err error) {
	var st unix.Statfs_t
	if err := unix.Statfs(path, &st); err != nil {
		return 0, 0, err
	}
	return st.Blocks * uint64(st.Bsize), st.Bavail * uint64(st.Bsize), nil
}

// output runs a command and returns its trimmed stdout.
func output(ctx context.Context, timeout time.Duration, name string, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	out, err := exec.CommandContext(ctx, name, args...).Output()
	return strings.TrimSpace(string(out)), err
}
