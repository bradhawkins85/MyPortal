//go:build !windows

package minidump

// Dir is empty off Windows: there are no blue screen dumps to report.
func Dir() string { return "" }
