package scanner

import (
	"context"
	"net"
	"reflect"
	"strconv"
	"testing"
	"time"
)

func listenLocal(t *testing.T) (int, func()) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			conn.Close()
		}
	}()
	_, port, _ := net.SplitHostPort(ln.Addr().String())
	n, _ := strconv.Atoi(port)
	return n, func() { ln.Close() }
}

func closedLocalPort(t *testing.T) int {
	t.Helper()
	port, stop := listenLocal(t)
	stop()
	return port
}

func TestCheckPortsReportsOnlyOpenPorts(t *testing.T) {
	open, stop := listenLocal(t)
	defer stop()
	closed := closedLocalPort(t)

	got := CheckPorts(context.Background(), "127.0.0.1", []int{closed, open}, time.Second, 4)

	if !reflect.DeepEqual(got, []int{open}) {
		t.Fatalf("open ports = %v, want [%d]", got, open)
	}
}

func TestCheckPortsStopsWhenCancelled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got := CheckPorts(ctx, "127.0.0.1", []int{1, 2, 3}, time.Second, 2); len(got) != 0 {
		t.Fatalf("cancelled check reported %v", got)
	}
}

func TestIPInTargets(t *testing.T) {
	targets := []string{"192.168.1.0/24", "10.0.0.0/16"}
	for ip, want := range map[string]bool{
		"192.168.1.20": true,
		"10.0.200.1":   true,
		"192.168.2.1":  false,
		"not-an-ip":    false,
	} {
		if got := IPInTargets(ip, targets); got != want {
			t.Errorf("IPInTargets(%q) = %v, want %v", ip, got, want)
		}
	}
	if IPInTargets("192.168.1.20", nil) {
		t.Error("no targets must allow nothing")
	}
}
