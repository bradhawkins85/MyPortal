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

// verifyPipeServer checks the process serving the pipe runs as LocalSystem,
// so the UI never takes chats or notifications from a user's impostor.
func verifyPipeServer(conn net.Conn) error {
	f, ok := conn.(interface{ Fd() uintptr })
	if !ok {
		return fmt.Errorf("ipc: pipe handle unavailable")
	}
	var pid uint32
	if err := windows.GetNamedPipeServerProcessId(windows.Handle(f.Fd()), &pid); err != nil {
		return fmt.Errorf("ipc: pipe server process: %w", err)
	}
	proc, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return fmt.Errorf("ipc: open pipe server process: %w", err)
	}
	defer windows.CloseHandle(proc) //nolint:errcheck
	var token windows.Token
	if err := windows.OpenProcessToken(proc, windows.TOKEN_QUERY, &token); err != nil {
		return fmt.Errorf("ipc: pipe server token: %w", err)
	}
	defer token.Close() //nolint:errcheck
	user, err := token.GetTokenUser()
	if err != nil {
		return fmt.Errorf("ipc: pipe server user: %w", err)
	}
	if !user.User.Sid.IsWellKnown(windows.WinLocalSystemSid) {
		return fmt.Errorf("ipc: pipe server is not running as LocalSystem")
	}
	return nil
}

func removeSocket(_ string) {}
