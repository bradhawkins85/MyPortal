//go:build windows

package ipc

import (
	"net"
	"os"
)

func listenPlatform(path string) (net.Listener, error) {
	// Named pipe support requires github.com/Microsoft/go-winio which is
	// an optional build dependency.  For the MVP we fall back to TCP
	// localhost so the code compiles and runs on Windows without cgo.
	return net.Listen("tcp", "127.0.0.1:47832")
}

func dialPlatform(_ string) (net.Conn, error) {
	return net.Dial("tcp", "127.0.0.1:47832")
}

func removeSocket(_ string) {
	// Nothing to remove on Windows (TCP).
	_ = os.DevNull
}
