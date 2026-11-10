package release

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/pem"
	"errors"
	"strings"
	"testing"
)

const digest = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"

func testKey(t *testing.T) (ed25519.PublicKey, ed25519.PrivateKey) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return pub, priv
}

func sign(priv ed25519.PrivateKey, asset, version, sha string) string {
	return base64.StdEncoding.EncodeToString(ed25519.Sign(priv, Manifest(asset, version, sha)))
}

func TestVerifyAcceptsTheSignedBuild(t *testing.T) {
	pub, priv := testKey(t)
	sig := sign(priv, "myportal-tray.msi", "v1.2.3", digest)
	if err := verifyWith(pub, "myportal-tray.msi", "1.2.3", strings.ToUpper(digest), sig); err != nil {
		t.Fatal(err)
	}
}

func TestVerifyRejectsAnythingElse(t *testing.T) {
	pub, priv := testKey(t)
	otherPub, _ := testKey(t)
	sig := sign(priv, "myportal-tray.msi", "1.2.3", digest)
	other := strings.Repeat("0", 64)
	cases := map[string]error{
		"other platform": verifyWith(pub, "myportal-tray.pkg", "1.2.3", digest, sig),
		"older version":  verifyWith(pub, "myportal-tray.msi", "1.2.2", digest, sig),
		"other bytes":    verifyWith(pub, "myportal-tray.msi", "1.2.3", other, sig),
		"other key":      verifyWith(otherPub, "myportal-tray.msi", "1.2.3", digest, sig),
		"unsigned":       verifyWith(pub, "myportal-tray.msi", "1.2.3", digest, ""),
		"garbage":        verifyWith(pub, "myportal-tray.msi", "1.2.3", digest, "not base64!"),
		"bad digest":     verifyWith(pub, "myportal-tray.msi", "1.2.3", "abc", sig),
	}
	for name, err := range cases {
		if err == nil {
			t.Errorf("%s: accepted", name)
		}
	}
	if err := verifyWith(nil, "myportal-tray.msi", "1.2.3", digest, sig); !errors.Is(err, ErrNoKey) {
		t.Errorf("no key: %v", err)
	}
}

func TestManifestMatchesTheReleaseWorkflow(t *testing.T) {
	// .github/workflows/sign-release.yml writes exactly these lines.
	want := "myportal-release-v1\nasset=myportal-rmm-linux-amd64\nversion=2.0.1\nsha256=" + digest + "\n"
	if got := string(Manifest("myportal-rmm-linux-amd64", "v2.0.1", digest)); got != want {
		t.Fatalf("manifest = %q", got)
	}
}

func TestParsePublicKey(t *testing.T) {
	pub, _ := testKey(t)
	der, err := x509.MarshalPKIXPublicKey(pub)
	if err != nil {
		t.Fatal(err)
	}
	text := string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: der}))
	got, err := ParsePublicKey(text)
	if err != nil || !got.Equal(pub) {
		t.Fatalf("ParsePublicKey = %v, %v", got, err)
	}
	if _, err := ParsePublicKey("junk"); err == nil {
		t.Fatal("accepted junk")
	}
}

func TestBuiltInKeyIsValidWhenSet(t *testing.T) {
	if strings.TrimSpace(PublicKeyPEM) == "" {
		t.Skip("release key not set yet")
	}
	if len(publicKey) != ed25519.PublicKeySize {
		t.Fatal("PublicKeyPEM does not parse as an Ed25519 key")
	}
}
