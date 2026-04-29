//go:build !windows

package ipc

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// withTempSocket overrides the platform socket path for the duration of a
// single test so we don't conflict with a running production socket.
func withTempSocket(t *testing.T) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("named-pipe path is hard-coded on Windows; covered by integration tests")
	}
	dir := t.TempDir()
	path := filepath.Join(dir, "ipc.sock")
	// SocketPath() is hard-coded; use a custom listener / dialer instead so
	// tests don't depend on /tmp paths leaking between concurrent runs.
	return path
}

func TestOnConnectFiresForLateClient(t *testing.T) {
	path := withTempSocket(t)

	ln, err := listenSocket(path)
	if err != nil {
		t.Fatalf("listenSocket: %v", err)
	}
	defer ln.Close()

	srv := &Server{ln: ln, handlers: make(map[string]Handler)}
	go srv.acceptLoop()

	got := make(chan Message, 1)
	srv.OnConnect(func(send func(Message)) {
		send(Message{Type: "config_changed", Payload: json.RawMessage(`{"version":7}`)})
	})

	conn, err := dialSocket(path)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()

	go func() {
		msg, err := ReadFrom(conn)
		if err != nil {
			t.Errorf("ReadFrom: %v", err)
			return
		}
		got <- *msg
	}()

	select {
	case msg := <-got:
		if msg.Type != "config_changed" {
			t.Fatalf("unexpected message type %q", msg.Type)
		}
	case <-time.After(2 * time.Second):
		t.Fatalf("timeout waiting for OnConnect-replayed message")
	}

	_ = os.Remove(path)
}
