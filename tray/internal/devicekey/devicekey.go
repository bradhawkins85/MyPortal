// Package devicekey keeps the tray device's own Ed25519 key pair.
//
// The service makes the key once, keeps the private half in a file only
// SYSTEM/root and Administrators can read, and registers the public half with
// the portal. When the WebSocket connects, the portal sends a fresh nonce
// and the service signs it, so a copied bearer token is not enough to pose
// as this device.
package devicekey

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/secdir"
)

// FileName is the key file inside the tray state folder.
const FileName = "device-key"

// LoadOrCreate reads the key in dir, making and saving a new one when there is
// none or the file is unreadable as a key.
func LoadOrCreate(dir string) (ed25519.PrivateKey, error) {
	path := filepath.Join(dir, FileName)
	if data, err := os.ReadFile(path); err == nil {
		if key, err := parse(data); err == nil {
			// Repair permissions on every start, in case they were changed.
			if err := secdir.SecureFile(path, secdir.Private); err != nil {
				return nil, fmt.Errorf("secure device key: %w", err)
			}
			return key, nil
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, fmt.Errorf("read device key: %w", err)
	}
	_, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate device key: %w", err)
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	tmp, err := os.CreateTemp(dir, FileName+".*")
	if err != nil {
		return nil, err
	}
	tmpPath := tmp.Name()
	defer os.Remove(tmpPath)
	if _, err := tmp.WriteString(base64.StdEncoding.EncodeToString(key.Seed()) + "\n"); err != nil {
		tmp.Close()
		return nil, err
	}
	if err := tmp.Close(); err != nil {
		return nil, err
	}
	if err := secdir.SecureFile(tmpPath, secdir.Private); err != nil {
		return nil, fmt.Errorf("secure device key: %w", err)
	}
	if err := os.Rename(tmpPath, path); err != nil {
		return nil, err
	}
	return key, nil
}

func parse(data []byte) (ed25519.PrivateKey, error) {
	seed, err := base64.StdEncoding.DecodeString(strings.TrimSpace(string(data)))
	if err != nil {
		return nil, err
	}
	if len(seed) != ed25519.SeedSize {
		return nil, errors.New("device key has the wrong length")
	}
	return ed25519.NewKeyFromSeed(seed), nil
}

// PublicKey is the key's public half as the portal stores it.
func PublicKey(key ed25519.PrivateKey) string {
	return base64.StdEncoding.EncodeToString(key.Public().(ed25519.PublicKey))
}

// ChallengeMessage is what the device signs to answer a WebSocket challenge.
func ChallengeMessage(deviceUID, nonce string) []byte {
	return []byte("myportal-tray-ws-v1\ndevice_uid=" + deviceUID + "\nnonce=" + nonce + "\n")
}

// SignChallenge answers the portal's WebSocket challenge.
func SignChallenge(key ed25519.PrivateKey, deviceUID, nonce string) string {
	return base64.StdEncoding.EncodeToString(ed25519.Sign(key, ChallengeMessage(deviceUID, nonce)))
}
