package main

import (
	"encoding/json"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/ipc"
)

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
