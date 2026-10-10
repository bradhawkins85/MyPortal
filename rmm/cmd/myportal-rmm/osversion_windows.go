//go:build windows

package main

import "golang.org/x/sys/windows"

func osVersion() string {
	info := windows.RtlGetVersion()
	return windowsVersion(info.MajorVersion, info.MinorVersion, info.BuildNumber)
}
