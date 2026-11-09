//go:build !windows

package main

// secureStateDirs is a no-op outside Windows: the state folder sits under
// root-owned locations that other users cannot create entries in.
func secureStateDirs() []string { return nil }
