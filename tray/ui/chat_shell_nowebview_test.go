//go:build nowebview

// chat_shell_nowebview_test.go tests the chat shell discovery helpers and the
// chatClientMode helper.  These tests are compiled for all nowebview platforms
// (Windows, macOS, Linux) and are run by the standard tray test suite:
//
//	go test -tags nowebview -count=1 ./...
package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// ---------------------------------------------------------------------------
// fileExists
// ---------------------------------------------------------------------------

func TestFileExists_ExistingFile(t *testing.T) {
	f, err := os.CreateTemp(t.TempDir(), "fileexists-*")
	if err != nil {
		t.Fatal(err)
	}
	f.Close()
	if !fileExists(f.Name()) {
		t.Errorf("expected true for %q", f.Name())
	}
}

func TestFileExists_AbsentFile(t *testing.T) {
	p := filepath.Join(t.TempDir(), "not-present")
	if fileExists(p) {
		t.Errorf("expected false for non-existent path %q", p)
	}
}

// ---------------------------------------------------------------------------
// findChatShellInDir
// ---------------------------------------------------------------------------

func TestFindChatShellInDir_NotFound(t *testing.T) {
	dir := t.TempDir()
	if result := findChatShellInDir(dir); result != "" {
		t.Errorf("expected empty string, got %q", result)
	}
}

func TestFindChatShellInDir_Found(t *testing.T) {
	dir := t.TempDir()
	name := chatShellBinaryName
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	target := filepath.Join(dir, name)
	if err := os.WriteFile(target, []byte("fake"), 0o755); err != nil {
		t.Fatal(err)
	}
	result := findChatShellInDir(dir)
	if result != target {
		t.Errorf("expected %q, got %q", target, result)
	}
}

func TestFindChatShellInDir_IgnoresWrongName(t *testing.T) {
	dir := t.TempDir()
	// Place a file with the wrong name.
	wrong := filepath.Join(dir, "something-else")
	if err := os.WriteFile(wrong, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	if result := findChatShellInDir(dir); result != "" {
		t.Errorf("expected empty for mismatched name, got %q", result)
	}
}

// ---------------------------------------------------------------------------
// chatClientMode
// ---------------------------------------------------------------------------

func TestChatClientMode_DefaultsToApp(t *testing.T) {
	prev := gConfig
	gConfig = nil
	defer func() { gConfig = prev }()

	if m := chatClientMode(); m != "app" {
		t.Errorf("expected \"app\", got %q", m)
	}
}

func TestChatClientMode_ReadsFromConfig(t *testing.T) {
	prev := gConfig
	gConfig = &api.ConfigResponse{ChatClientMode: "browser"}
	defer func() { gConfig = prev }()

	if m := chatClientMode(); m != "browser" {
		t.Errorf("expected \"browser\", got %q", m)
	}
}

func TestChatClientMode_EmptyFieldDefaultsToApp(t *testing.T) {
	prev := gConfig
	gConfig = &api.ConfigResponse{ChatClientMode: ""}
	defer func() { gConfig = prev }()

	if m := chatClientMode(); m != "app" {
		t.Errorf("expected \"app\" for empty field, got %q", m)
	}
}

func TestChatClientMode_ShellMode(t *testing.T) {
	prev := gConfig
	gConfig = &api.ConfigResponse{ChatClientMode: "shell"}
	defer func() { gConfig = prev }()

	if m := chatClientMode(); m != "shell" {
		t.Errorf("expected \"shell\", got %q", m)
	}
}

// ---------------------------------------------------------------------------
// openWithChatShell — launch path
// ---------------------------------------------------------------------------

// TestOpenWithChatShell_ReturnsFalseWhenAbsent verifies that openWithChatShell
// gracefully returns false when the chat shell binary is not installed rather
// than panicking or erroring.
func TestOpenWithChatShell_ReturnsFalseWhenAbsent(t *testing.T) {
	// findChatShell searches next to the test binary and in well-known paths.
	// Unless the test is running inside the actual install directory (unlikely
	// in CI), the shell binary won't be found.
	name := chatShellBinaryName
	if runtime.GOOS == "windows" {
		name += ".exe"
	}

	// Ensure there is no accidentally-present shell binary next to the test exe.
	self, err := os.Executable()
	if err != nil {
		t.Skip("cannot determine executable path")
	}
	candidate := filepath.Join(filepath.Dir(self), name)
	if fileExists(candidate) {
		t.Skipf("chat shell binary present at %s — skipping absence test", candidate)
	}

	result := openWithChatShell("https://example.com/tray/chat", nil)
	if result {
		t.Error("expected false when chat shell is not installed, got true")
	}
}
