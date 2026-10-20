//go:build !windows && !darwin

package main

import "fmt"

func launchTrayUIForActiveUser() error {
	return fmt.Errorf("active-user UI launch is not implemented on this platform")
}
