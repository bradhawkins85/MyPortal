package main

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/config"
	"github.com/bradhawkins85/myportal-tray/internal/devicekey"
)

func useTempStateDir(t *testing.T) {
	t.Helper()
	previous := stateDir
	dir := t.TempDir()
	stateDir = func() string { return dir }
	t.Cleanup(func() { stateDir = previous })
}

func TestServiceAnswersTheWebSocketChallenge(t *testing.T) {
	useTempStateDir(t)
	registered := make(chan string, 4)
	answer := make(chan map[string]string, 1)
	upgrader := websocket.Upgrader{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/tray/device-key":
			var body map[string]string
			_ = json.NewDecoder(r.Body).Decode(&body)
			registered <- body["public_key"]
		case strings.HasPrefix(r.URL.Path, "/ws/tray/"):
			conn, err := upgrader.Upgrade(w, r, nil)
			if err != nil {
				t.Error(err)
				return
			}
			defer conn.Close()
			_ = conn.WriteJSON(map[string]string{"type": "challenge", "nonce": "nonce-1"})
			for {
				var msg map[string]string
				if err := conn.ReadJSON(&msg); err != nil {
					return
				}
				if msg["type"] == "auth" {
					answer <- msg
					return
				}
			}
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer server.Close()

	d := newDaemon(&config.Config{PortalURL: server.URL})
	d.client = api.New(server.URL)
	d.client.SetAuth("uid-1", "device-token")
	d.loadDeviceKey()
	if d.deviceKey == nil {
		t.Fatal("no device key")
	}
	d.registerDeviceKey()
	if got := <-registered; got != devicekey.PublicKey(d.deviceKey) {
		t.Fatalf("registered %q", got)
	}

	conn, err := d.client.ConnectWS(t.Context())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	go d.handleWS(t.Context(), conn)

	select {
	case msg := <-answer:
		sig, _ := base64.StdEncoding.DecodeString(msg["signature"])
		pub := d.deviceKey.Public().(ed25519.PublicKey)
		if !ed25519.Verify(pub, devicekey.ChallengeMessage("uid-1", "nonce-1"), sig) {
			t.Fatal("challenge answer does not verify")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the service did not answer the challenge")
	}
}

func TestDeviceKeySurvivesRestart(t *testing.T) {
	useTempStateDir(t)
	first := newDaemon(&config.Config{PortalURL: "https://portal.test"})
	first.loadDeviceKey()
	second := newDaemon(&config.Config{PortalURL: "https://portal.test"})
	second.loadDeviceKey()
	if first.devicePublicKey() == "" || first.devicePublicKey() != second.devicePublicKey() {
		t.Fatal("device key changed across a service restart")
	}
}
