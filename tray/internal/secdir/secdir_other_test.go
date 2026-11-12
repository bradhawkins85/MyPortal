//go:build !windows

package secdir

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSecureCreatesPrivateFolder(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "state")
	if err := Secure(dir, Private); err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(dir)
	if err != nil {
		t.Fatal(err)
	}
	if got := info.Mode().Perm(); got != 0o700 {
		t.Fatalf("mode = %o, want 700", got)
	}
}

func TestSecureTightensExistingFolder(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "state")
	if err := os.Mkdir(dir, 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(dir, 0o777); err != nil {
		t.Fatal(err)
	}
	if err := Secure(dir, Private); err != nil {
		t.Fatal(err)
	}
	info, _ := os.Stat(dir)
	if got := info.Mode().Perm(); got != 0o700 {
		t.Fatalf("mode = %o, want 700", got)
	}
}

func TestSecureUsersReadFolder(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "tray")
	if err := Secure(dir, UsersRead); err != nil {
		t.Fatal(err)
	}
	info, _ := os.Stat(dir)
	if got := info.Mode().Perm(); got != 0o755 {
		t.Fatalf("mode = %o, want 755", got)
	}
}

func TestSecureRefusesSymlink(t *testing.T) {
	base := t.TempDir()
	target := filepath.Join(base, "elsewhere")
	if err := os.Mkdir(target, 0o700); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(base, "state")
	if err := os.Symlink(target, link); err != nil {
		t.Skip(err)
	}
	// MkdirAll follows the link, so Secure must notice it is not a folder.
	if err := Secure(link, Private); err == nil {
		t.Fatal("Secure accepted a symbolic link")
	}
}

func TestSecureFileModes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	if err := os.WriteFile(path, []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := SecureFile(path, Private); err != nil {
		t.Fatal(err)
	}
	if info, _ := os.Stat(path); info.Mode().Perm() != 0o600 {
		t.Fatalf("private mode = %o, want 600", info.Mode().Perm())
	}
	if err := SecureFile(path, UsersRead); err != nil {
		t.Fatal(err)
	}
	if info, _ := os.Stat(path); info.Mode().Perm() != 0o644 {
		t.Fatalf("users-read mode = %o, want 644", info.Mode().Perm())
	}
}
