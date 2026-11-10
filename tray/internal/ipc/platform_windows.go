//go:build windows

package ipc

import (
	"fmt"
	"net"
	"time"

	"github.com/Microsoft/go-winio"
	"golang.org/x/sys/windows"
)

// pipeSDDL lets SYSTEM and Administrators own the pipe and signed-in users
// connect to it. The pipe is created with FILE_CREATE (first instance), so
// a process that created the name first makes the service fail and retry
// rather than share the pipe with it.
const pipeSDDL = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;AU)"

func listenPlatform(path string) (net.Listener, error) {
	return winio.ListenPipe(path, &winio.PipeConfig{SecurityDescriptor: pipeSDDL})
}

func dialPlatform(path string) (net.Conn, error) {
	timeout := 5 * time.Second
	conn, err := winio.DialPipe(path, &timeout)
	if err != nil {
		return nil, err
	}
	if err := verifyPipeServer(conn); err != nil {
		_ = conn.Close()
		return nil, err
	}
	return conn, nil
}

// verifyPipeServer checks the pipe is owned by LocalSystem or the
// Administrators group, so the UI never takes chats or notifications from a
// standard user's impostor pipe. It reads the pipe's owner rather than the
// server process token, which standard users are not allowed to open.
func verifyPipeServer(conn net.Conn) error {
	f, ok := conn.(interface{ Fd() uintptr })
	if !ok {
		return fmt.Errorf("ipc: pipe handle unavailable")
	}
	sd, err := windows.GetSecurityInfo(windows.Handle(f.Fd()), windows.SE_KERNEL_OBJECT, windows.OWNER_SECURITY_INFORMATION)
	if err != nil {
		return fmt.Errorf("ipc: pipe owner: %w", err)
	}
	owner, _, err := sd.Owner()
	if err != nil || owner == nil {
		return fmt.Errorf("ipc: pipe owner: %v", err)
	}
	if owner.IsWellKnown(windows.WinLocalSystemSid) || owner.IsWellKnown(windows.WinBuiltinAdministratorsSid) {
		return nil
	}
	return fmt.Errorf("ipc: pipe is owned by %s, not LocalSystem or Administrators", owner.String())
}

func removeSocket(_ string) {}
