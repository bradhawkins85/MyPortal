//go:build !windows

package defender

func executePowerShell(string) error { return ErrUnsupported }
