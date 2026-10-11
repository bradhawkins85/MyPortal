//go:build !windows

package eventlog

// Supported is true where Windows event logs can be read.
const Supported = false

// NewSource returns nil: there are no Windows event logs here.
func NewSource() Source { return nil }
