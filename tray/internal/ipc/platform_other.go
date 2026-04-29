//go:build !windows

package ipc

import (
	"net"
	"os"
)

func listenPlatform(_ string) (net.Listener, error) {
	panic("listenPlatform called on non-windows")
}

func dialPlatform(_ string) (net.Conn, error) {
	panic("dialPlatform called on non-windows")
}

func removeSocket(path string) {
	_ = os.Remove(path)
}
