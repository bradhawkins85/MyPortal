//go:build linux || darwin

package live

func fileRoots() []string { return []string{"/"} }
