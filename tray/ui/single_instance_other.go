//go:build !windows

package main

func acquireSingleInstanceLock() (bool, error) {
	return true, nil
}

func releaseSingleInstanceLock() {}
