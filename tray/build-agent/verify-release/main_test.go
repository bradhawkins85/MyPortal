package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const commit = "0123456789abcdef0123456789abcdef01234567"

func writeKeyAndSig(t *testing.T, tag, signedCommit string) (dir string) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	der, _ := x509.MarshalPKIXPublicKey(pub)
	dir = t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "key.pem"), pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}), 0o600); err != nil {
		t.Fatal(err)
	}
	sig := base64.StdEncoding.EncodeToString(ed25519.Sign(priv, sourceManifest(tag, signedCommit)))
	if err := os.WriteFile(filepath.Join(dir, "source.sig"), []byte(sig+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return dir
}

func args(dir, tag, commit string) []string {
	return []string{"-key", filepath.Join(dir, "key.pem"), "-tag", tag, "-commit", commit, "-sig", filepath.Join(dir, "source.sig")}
}

func TestVerifiesTheSignedCommit(t *testing.T) {
	dir := writeKeyAndSig(t, "v1.2.3", commit)
	if err := run(args(dir, "v1.2.3", strings.ToUpper(commit))); err != nil {
		t.Fatal(err)
	}
}

func TestRefusesAMovedTagOrAnotherRelease(t *testing.T) {
	dir := writeKeyAndSig(t, "v1.2.3", commit)
	other := strings.Repeat("f", 40)
	if err := run(args(dir, "v1.2.3", other)); err == nil {
		t.Fatal("accepted a tag moved to another commit")
	}
	if err := run(args(dir, "v1.2.4", commit)); err == nil {
		t.Fatal("accepted another release's signature")
	}
	if err := run(args(dir, "v1.2.3", "abc")); err == nil {
		t.Fatal("accepted a short commit")
	}
}

func TestManifestMatchesTheSignReleaseWorkflow(t *testing.T) {
	want := "myportal-source-v1\ntag=v1.2.3\ncommit=" + commit + "\n"
	if got := string(sourceManifest("v1.2.3", commit)); got != want {
		t.Fatalf("manifest = %q", got)
	}
}
