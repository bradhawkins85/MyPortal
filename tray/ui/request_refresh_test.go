package main

import (
	"net"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/ipc"
)

func TestRequestConfigRefreshSendsIPCMessage(t *testing.T) {
	serverConn, clientConn := net.Pipe()
	defer serverConn.Close()
	defer clientConn.Close()

	previousConn := gIPCConn
	gIPCConn = clientConn
	defer func() {
		gIPCConn = previousConn
	}()

	done := make(chan *ipc.Message, 1)
	errCh := make(chan error, 1)
	go func() {
		msg, err := ipc.ReadFrom(serverConn)
		if err != nil {
			errCh <- err
			return
		}
		done <- msg
	}()

	requestConfigRefresh()

	select {
	case err := <-errCh:
		t.Fatalf("expected refresh_config IPC message, got error: %v", err)
	case msg := <-done:
		if msg.Type != "refresh_config" {
			t.Fatalf("expected refresh_config IPC message, got %q", msg.Type)
		}
	}
}
