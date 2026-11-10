package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"

	"github.com/bradhawkins85/myportal-rmm/internal/agentkey"
	"github.com/bradhawkins85/myportal-rmm/internal/state"
)

func TestEnrolmentKeyProvesTheExistingKey(t *testing.T) {
	key, encoded, err := agentkey.New()
	if err != nil {
		t.Fatal(err)
	}
	existing := state.State{AgentUID: "uid-1", AuthToken: "old", PrivateKey: encoded}

	kept, enrolKey, err := enrolmentKey(existing, "uid-1", "old")
	if err != nil || kept != encoded {
		t.Fatalf("existing key not kept: %v", err)
	}
	pub := key.Public().(ed25519.PublicKey)
	proof, _ := base64.StdEncoding.DecodeString(enrolKey.Proof)
	if enrolKey.PublicKey != agentkey.PublicKey(key) ||
		!ed25519.Verify(pub, agentkey.EnrolMessage("uid-1", "old", enrolKey.PublicKey), proof) {
		t.Fatalf("re-enrolment proof does not verify: %#v", enrolKey)
	}

	_, firstTime, err := enrolmentKey(state.State{}, "uid-new", "")
	if err != nil || firstTime.PublicKey == "" || firstTime.Proof != "" {
		t.Fatalf("first enrolment key = %#v, %v", firstTime, err)
	}
	if fresh, other, _ := enrolmentKey(existing, "uid-2", ""); fresh == encoded || other.Proof != "" {
		t.Fatal("a new agent UID must get a new key and no proof")
	}
}

func TestLiveAnswerSignsWithTheAgentKey(t *testing.T) {
	key, encoded, _ := agentkey.New()
	answer := liveAnswer(state.State{AgentUID: "uid-1", PrivateKey: encoded}, 9)
	sig, _ := base64.StdEncoding.DecodeString(answer("nonce"))
	if !ed25519.Verify(key.Public().(ed25519.PublicKey), agentkey.LiveMessage("uid-1", 9, "nonce"), sig) {
		t.Fatal("live answer does not verify")
	}
	if liveAnswer(state.State{}, 9) != nil {
		t.Fatal("an agent without a key must not answer")
	}
}
