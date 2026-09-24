package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/ipc"
)

func TestServiceDoesNotModifyDefenderPreferences(t *testing.T) {
	source, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}

	for _, forbidden := range []string{"ApplyExclusions", "Add-MpPreference", "Set-MpPreference", "TamperProtection"} {
		if strings.Contains(string(source), forbidden) {
			t.Errorf("tray service must not attempt to modify Defender preferences: found %q", forbidden)
		}
	}
}

func TestDeliverUserSessionMessageQueuesAndLaunchesUIWhenNoIPCClient(t *testing.T) {
	d := &daemon{}

	previousLaunch := launchTrayUIForActiveUserFunc
	launches := 0
	launchTrayUIForActiveUserFunc = func() error {
		launches++
		return nil
	}
	t.Cleanup(func() { launchTrayUIForActiveUserFunc = previousLaunch })

	msg := ipc.Message{
		Type:    "chat_open",
		Payload: json.RawMessage(`{"room_id":77}`),
	}
	d.deliverUserSessionMessage(msg)

	if launches != 1 {
		t.Fatalf("launches = %d, want 1", launches)
	}
	pending := d.consumePendingUIMessage()
	if pending == nil {
		t.Fatal("expected pending UI message")
	}
	if pending.Type != msg.Type || string(pending.Payload) != string(msg.Payload) {
		t.Fatalf("pending = %#v, want %#v", pending, msg)
	}
	if again := d.consumePendingUIMessage(); again != nil {
		t.Fatalf("pending message was not consumed: %#v", again)
	}
}

func TestProcessDefenderCommandsDoesNotPollCommandsWhenExcluded(t *testing.T) {
	commandPolls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/tray/defender/policy":
			_ = json.NewEncoder(w).Encode(api.DefenderPolicy{Enabled: false})
		case "/api/tray/defender/commands":
			commandPolls++
			_ = json.NewEncoder(w).Encode(map[string]interface{}{"commands": []api.DefenderCommand{}})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	d := &daemon{client: api.New(server.URL)}
	d.processDefenderCommands()

	if commandPolls != 0 {
		t.Fatalf("command polls = %d, want 0 for an excluded device", commandPolls)
	}
}

func TestProcessDefenderCommandsRechecksPolicyAfterClaim(t *testing.T) {
	policyChecks := 0
	resultUploads := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/tray/defender/policy":
			policyChecks++
			_ = json.NewEncoder(w).Encode(api.DefenderPolicy{Enabled: policyChecks == 1})
		case "/api/tray/defender/commands":
			_ = json.NewEncoder(w).Encode(map[string]interface{}{"commands": []api.DefenderCommand{{
				ID: 17, CommandType: "quick_scan",
			}}})
		case "/api/tray/defender/commands/17/result":
			resultUploads++
			w.WriteHeader(http.StatusOK)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	d := &daemon{client: api.New(server.URL)}
	d.processDefenderCommands()

	if policyChecks != 2 {
		t.Fatalf("policy checks = %d, want 2", policyChecks)
	}
	if resultUploads != 0 {
		t.Fatalf("result uploads = %d, want 0 because the command must not execute", resultUploads)
	}
}
