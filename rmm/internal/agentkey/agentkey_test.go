package agentkey

import (
	"crypto/ed25519"
	"encoding/base64"
	"testing"
)

func TestKeyRoundTripsAndSigns(t *testing.T) {
	key, encoded, err := New()
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := Parse(encoded)
	if err != nil || !parsed.Equal(key) {
		t.Fatalf("Parse = %v, %v", parsed, err)
	}
	if _, err := Parse("bm90IGEga2V5"); err == nil {
		t.Fatal("a short key was accepted")
	}
	pub, _ := base64.StdEncoding.DecodeString(PublicKey(key))
	msg := LiveMessage("agent-1", 42, "n1")
	sig, _ := base64.StdEncoding.DecodeString(Sign(key, msg))
	if !ed25519.Verify(pub, msg, sig) {
		t.Fatal("signature does not verify")
	}
}

func TestMessagesMatchThePortal(t *testing.T) {
	if got := string(LiveMessage("a", 7, "n")); got != "myportal-rmm-live-v1\nagent_uid=a\nsession_id=7\nnonce=n\n" {
		t.Fatalf("live message = %q", got)
	}
	want := "myportal-rmm-enrol-v1\nagent_uid=a\ncurrent_token_sha256=" +
		"2c26b46b68ffc68ff99b453c1d30413413422d706483bfa0f98a5e886266e7ae\npublic_key=pk\n"
	if got := string(EnrolMessage("a", "foo", "pk")); got != want {
		t.Fatalf("enrol message = %q", got)
	}
}
