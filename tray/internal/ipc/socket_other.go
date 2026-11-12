//go:build !darwin && !linux

package ipc

import "net"

func configureSocket(_ string) error {
	return nil
}

// checkPeer accepts every client. On Windows the named pipe's access list
// decides who may connect.
func checkPeer(net.Conn) error { return nil }
