//go:build !linux

package proxmox

import "context"

// Detect reports whether this device is a Proxmox VE host; only Linux can be.
func Detect() bool { return false }

// Collect is not supported off Linux.
func Collect(context.Context, bool) Status {
	return Status{Errors: []string{"this device is not a Proxmox VE host"}}
}
