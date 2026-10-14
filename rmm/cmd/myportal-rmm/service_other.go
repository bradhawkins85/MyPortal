//go:build !windows

package main

func platformServiceNotInstalled(err error) bool { return false }
