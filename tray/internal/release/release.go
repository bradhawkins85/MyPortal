// Package release verifies that a downloaded MyPortal build was signed by the
// project's release key.
//
// The Sign release workflow signs every release asset with an Ed25519 key
// held in a protected GitHub environment. MyPortal relays each signature to
// devices but cannot make one, so a compromised portal (or anyone between the
// device and GitHub) cannot get a device to run a build the project did not
// release. The signature covers the asset name, the release version and the
// file's SHA-256, so a signed build cannot be passed off as another platform's
// installer or as a different version.
package release

import (
	"crypto/ed25519"
	"crypto/x509"
	_ "embed"
	"encoding/base64"
	"encoding/hex"
	"encoding/pem"
	"errors"
	"fmt"
	"strings"
)

// PublicKeyPEM is the release signing public key. The private key never
// leaves the release-signing GitHub environment; the Sign release workflow
// refuses to run with a key that does not match this file.
//
//go:embed release-public-key.pem
var PublicKeyPEM string

var publicKey ed25519.PublicKey

func init() {
	key, err := ParsePublicKey(PublicKeyPEM)
	if err == nil {
		publicKey = key
	}
}

// ErrNoKey means this build has no release key, so nothing can be verified.
var ErrNoKey = errors.New("this build has no release signing key")

// ParsePublicKey reads an Ed25519 public key in PEM ("BEGIN PUBLIC KEY") form.
func ParsePublicKey(pemText string) (ed25519.PublicKey, error) {
	block, _ := pem.Decode([]byte(strings.TrimSpace(pemText)))
	if block == nil {
		return nil, errors.New("no PEM public key")
	}
	parsed, err := x509.ParsePKIXPublicKey(block.Bytes)
	if err != nil {
		return nil, err
	}
	key, ok := parsed.(ed25519.PublicKey)
	if !ok {
		return nil, errors.New("release key is not Ed25519")
	}
	return key, nil
}

// CanonicalVersion drops a leading "v" so tags and reported versions match.
func CanonicalVersion(version string) string {
	v := strings.TrimSpace(version)
	if len(v) > 1 && (v[0] == 'v' || v[0] == 'V') {
		v = v[1:]
	}
	return v
}

// Manifest is the exact text the release workflow signs for an asset.
func Manifest(assetName, version, sha256Hex string) []byte {
	return []byte("myportal-release-v1\n" +
		"asset=" + assetName + "\n" +
		"version=" + CanonicalVersion(version) + "\n" +
		"sha256=" + strings.ToLower(strings.TrimSpace(sha256Hex)) + "\n")
}

// Verify checks a base64 signature over the manifest for assetName, version
// and the SHA-256 of the bytes actually downloaded.
func Verify(assetName, version, sha256Hex, signature string) error {
	return verifyWith(publicKey, assetName, version, sha256Hex, signature)
}

func verifyWith(key ed25519.PublicKey, assetName, version, sha256Hex, signature string) error {
	if len(key) != ed25519.PublicKeySize {
		return ErrNoKey
	}
	if assetName == "" || CanonicalVersion(version) == "" {
		return errors.New("release signature: missing asset name or version")
	}
	digest, err := hex.DecodeString(strings.TrimSpace(sha256Hex))
	if err != nil || len(digest) != 32 {
		return errors.New("release signature: invalid SHA-256")
	}
	signature = strings.TrimSpace(signature)
	if signature == "" {
		return errors.New("release signature: the build is not signed")
	}
	sig, err := base64.StdEncoding.DecodeString(signature)
	if err != nil || len(sig) != ed25519.SignatureSize {
		return errors.New("release signature: malformed signature")
	}
	if !ed25519.Verify(key, Manifest(assetName, version, sha256Hex), sig) {
		return fmt.Errorf("release signature does not match %s %s", assetName, CanonicalVersion(version))
	}
	return nil
}

// UseKeyForTesting replaces the release key until restore is called. Only
// tests call it.
func UseKeyForTesting(key ed25519.PublicKey) (restore func()) {
	previous := publicKey
	publicKey = key
	return func() { publicKey = previous }
}
