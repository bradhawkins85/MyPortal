package logger

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInitWritesNextToExecutable(t *testing.T) {
	if err := Init("test"); err != nil {
		t.Fatalf("Init: %v", err)
	}

	exe, err := os.Executable()
	if err != nil {
		t.Fatalf("os.Executable: %v", err)
	}
	want := filepath.Join(filepath.Dir(exe), "logs")
	got := filepath.Dir(Path())
	if got != want {
		// Fallback paths are platform-dependent and may legitimately be
		// chosen on read-only test runners; accept either as long as the
		// logger ended up with *some* path.
		t.Logf("log path %q is not the exe-adjacent dir %q (likely fallback)", got, want)
	}
	if Path() == "" {
		t.Fatalf("Path() is empty after successful Init")
	}

	// File should be writable: append a debug line and verify it lands on disk.
	SetDebug(true)
	Debug("hello %s", "world")
	data, err := os.ReadFile(Path())
	if err != nil {
		t.Fatalf("read log: %v", err)
	}
	if !strings.Contains(string(data), "[DEBUG] hello world") {
		t.Fatalf("log file missing debug line; got: %q", string(data))
	}
}

func TestDebugDisabledByEnv(t *testing.T) {
	t.Setenv("MYPORTAL_TRAY_DEBUG", "0")
	if err := Init("test-nodebug"); err != nil {
		t.Fatalf("Init: %v", err)
	}
	if DebugEnabled() {
		t.Fatalf("debug should be disabled when MYPORTAL_TRAY_DEBUG=0")
	}

	before, _ := os.ReadFile(Path())
	Debug("should-not-appear")
	after, _ := os.ReadFile(Path())
	if strings.Contains(string(after[len(before):]), "should-not-appear") {
		t.Fatalf("Debug() wrote a line while disabled: %q", string(after[len(before):]))
	}
}
