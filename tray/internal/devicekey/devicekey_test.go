package devicekey

import (
	"crypto/ed25519"
	"encoding/base64"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestLoadOrCreateKeepsTheKey(t *testing.T) {
	dir := t.TempDir()
	first, err := LoadOrCreate(dir)
	if err != nil {
		t.Fatal(err)
	}
	second, err := LoadOrCreate(dir)
	if err != nil {
		t.Fatal(err)
	}
	if !first.Equal(second) {
		t.Fatal("the device key changed between starts")
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(filepath.Join(dir, FileName))
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm() != 0o600 {
			t.Fatalf("device key mode = %v, want 0600", info.Mode().Perm())
		}
	}
	leftovers, _ := filepath.Glob(filepath.Join(dir, FileName+".*"))
	if len(leftovers) != 0 {
		t.Fatalf("temporary key files left behind: %v", leftovers)
	}
}

func TestLoadOrCreateReplacesAnUnreadableKey(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, FileName), []byte("garbage"), 0o600); err != nil {
		t.Fatal(err)
	}
	key, err := LoadOrCreate(dir)
	if err != nil || len(key) != ed25519.PrivateKeySize {
		t.Fatalf("LoadOrCreate = %v, %v", key, err)
	}
}

func TestSignChallengeMatchesThePortal(t *testing.T) {
	key, err := LoadOrCreate(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if got := string(ChallengeMessage("abc", "n1")); got != "myportal-tray-ws-v1\ndevice_uid=abc\nnonce=n1\n" {
		t.Fatalf("challenge message = %q", got)
	}
	pub, err := base64.StdEncoding.DecodeString(PublicKey(key))
	if err != nil || len(pub) != ed25519.PublicKeySize {
		t.Fatalf("public key = %q", PublicKey(key))
	}
	sig, _ := base64.StdEncoding.DecodeString(SignChallenge(key, "abc", "n1"))
	if !ed25519.Verify(pub, ChallengeMessage("abc", "n1"), sig) {
		t.Fatal("signature does not verify")
	}
	if ed25519.Verify(pub, ChallengeMessage("abc", "n2"), sig) {
		t.Fatal("signature verifies for another nonce")
	}
}
