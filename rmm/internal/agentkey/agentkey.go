// Package agentkey holds the RMM agent's own Ed25519 key pair.
//
// The private key is kept in the agent's state file, which only the service
// account (and, on Windows, Administrators) can read. The public key is
// registered with MyPortal, which then needs a signature from it to re-key
// an agent that is in use and to open a live tools session.
package agentkey

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"strconv"
)

// New makes a key pair and returns it with its encoded form for the state file.
func New() (ed25519.PrivateKey, string, error) {
	_, key, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, "", err
	}
	return key, base64.StdEncoding.EncodeToString(key.Seed()), nil
}

// Parse reads a key saved by New.
func Parse(encoded string) (ed25519.PrivateKey, error) {
	seed, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		return nil, err
	}
	if len(seed) != ed25519.SeedSize {
		return nil, errors.New("agent key has the wrong length")
	}
	return ed25519.NewKeyFromSeed(seed), nil
}

// PublicKey is the key's public half as MyPortal stores it.
func PublicKey(key ed25519.PrivateKey) string {
	return base64.StdEncoding.EncodeToString(key.Public().(ed25519.PublicKey))
}

// EnrolMessage is what the agent's current key signs to re-key the agent.
func EnrolMessage(agentUID, currentToken, publicKey string) []byte {
	digest := sha256.Sum256([]byte(currentToken))
	return []byte("myportal-rmm-enrol-v1\nagent_uid=" + agentUID + "\ncurrent_token_sha256=" +
		hex.EncodeToString(digest[:]) + "\npublic_key=" + publicKey + "\n")
}

// LiveMessage is what the agent signs to answer a live tools session challenge.
func LiveMessage(agentUID string, sessionID int64, nonce string) []byte {
	return []byte("myportal-rmm-live-v1\nagent_uid=" + agentUID + "\nsession_id=" +
		strconv.FormatInt(sessionID, 10) + "\nnonce=" + nonce + "\n")
}

// Sign returns the base64 signature of message.
func Sign(key ed25519.PrivateKey, message []byte) string {
	return base64.StdEncoding.EncodeToString(ed25519.Sign(key, message))
}
