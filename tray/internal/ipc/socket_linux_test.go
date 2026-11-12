//go:build linux

package ipc

import (
	"net"
	"os"
	"path/filepath"
	"testing"
)

func TestPeerAllowedOnlyRootAndLoginAccounts(t *testing.T) {
	for uid, want := range map[uint32]bool{0: true, 1000: true, 60000: true, 33: false, 999: false, 60001: false, 65534: false} {
		if got := peerAllowed(uid, 1000, 60000); got != want {
			t.Errorf("peerAllowed(%d) = %v, want %v", uid, got, want)
		}
	}
}

func TestLoginUIDRangeReadsLoginDefs(t *testing.T) {
	dir := t.TempDir()
	defs := filepath.Join(dir, "login.defs")
	if err := os.WriteFile(defs, []byte("# comment\nUID_MIN\t\t 500\nUID_MAX 59999\nSYS_UID_MIN 100\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if low, high := loginUIDRange(defs); low != 500 || high != 59999 {
		t.Fatalf("range = %d-%d", low, high)
	}
	if low, high := loginUIDRange(filepath.Join(dir, "missing")); low != 1000 || high != 60000 {
		t.Fatalf("default range = %d-%d", low, high)
	}
	if err := os.WriteFile(defs, []byte("UID_MIN 0\nUID_MAX 70000\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if low, high := loginUIDRange(defs); low != 1000 || high != 60000 {
		t.Fatalf("an unsafe range must fall back to the default, got %d-%d", low, high)
	}
}

func TestPeerUIDReadsTheConnectingProcess(t *testing.T) {
	path := filepath.Join(t.TempDir(), "s.sock")
	ln, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()
	go func() {
		if c, err := net.Dial("unix", path); err == nil {
			defer c.Close()
			buf := make([]byte, 1)
			_, _ = c.Read(buf)
		}
	}()
	conn, err := ln.Accept()
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	uid, err := peerUID(conn)
	if err != nil {
		t.Fatal(err)
	}
	if uid != uint32(os.Getuid()) {
		t.Fatalf("peer uid = %d, want %d", uid, os.Getuid())
	}
}
