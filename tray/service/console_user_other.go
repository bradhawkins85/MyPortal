//go:build !windows && !darwin

package main

import "os"

// consoleUserName falls back to the service's own account, which is only
// meaningful when the service runs as the signed-in user.
func consoleUserName() string {
	for _, key := range []string{"USER", "USERNAME"} {
		if u := os.Getenv(key); u != "" && u != "root" {
			return u
		}
	}
	return ""
}
