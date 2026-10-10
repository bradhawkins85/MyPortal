package main

import "fmt"

func windowsVersion(major, minor, build uint32) string {
	return fmt.Sprintf("%d.%d.%d", major, minor, build)
}
