//go:build linux

package ipc

import (
	"bufio"
	"errors"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"syscall"
)

// configureSocket lets the tray UI, which runs as each signed-in desktop
// user, connect to the root service. Linux has no group every desktop user
// belongs to, so the socket file is open to local users and checkPeer
// decides who may stay connected.
func configureSocket(path string) error {
	if err := os.Chmod(path, 0666); err != nil {
		return fmt.Errorf("ipc: set socket permissions: %w", err)
	}
	return nil
}

// checkPeer keeps a connection only from root or a regular login account
// (UID_MIN..UID_MAX from /etc/login.defs). System accounts such as a web
// server or database user cannot send the service commands or read the chat
// and notifications it broadcasts to the tray UI.
func checkPeer(conn net.Conn) error {
	uid, err := peerUID(conn)
	if err != nil {
		return err
	}
	low, high := loginUIDRange("/etc/login.defs")
	if !peerAllowed(uid, low, high) {
		return fmt.Errorf("uid %d is not a login account", uid)
	}
	return nil
}

func peerAllowed(uid, low, high uint32) bool {
	return uid == 0 || (uid >= low && uid <= high)
}

func peerUID(conn net.Conn) (uint32, error) {
	unixConn, ok := conn.(*net.UnixConn)
	if !ok {
		return 0, errors.New("not a unix socket")
	}
	raw, err := unixConn.SyscallConn()
	if err != nil {
		return 0, err
	}
	var cred *syscall.Ucred
	var credErr error
	if err := raw.Control(func(fd uintptr) {
		cred, credErr = syscall.GetsockoptUcred(int(fd), syscall.SOL_SOCKET, syscall.SO_PEERCRED)
	}); err != nil {
		return 0, err
	}
	if credErr != nil {
		return 0, fmt.Errorf("read peer credentials: %w", credErr)
	}
	return cred.Uid, nil
}

// loginUIDRange reads UID_MIN and UID_MAX, falling back to the usual 1000
// and 60000. 65534 (nobody) is above UID_MAX on every common distribution.
func loginUIDRange(path string) (uint32, uint32) {
	low, high := uint32(1000), uint32(60000)
	f, err := os.Open(path)
	if err != nil {
		return low, high
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) < 2 || strings.HasPrefix(fields[0], "#") {
			continue
		}
		value, err := strconv.ParseUint(fields[1], 10, 32)
		if err != nil {
			continue
		}
		switch fields[0] {
		case "UID_MIN":
			low = uint32(value)
		case "UID_MAX":
			high = uint32(value)
		}
	}
	if low == 0 || high < low || high >= 65534 {
		return 1000, 60000
	}
	return low, high
}
