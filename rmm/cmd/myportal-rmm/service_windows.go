//go:build windows

package main

import (
	"errors"

	"golang.org/x/sys/windows"
)

func platformServiceNotInstalled(err error) bool {
	return errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST)
}
