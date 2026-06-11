//go:build nowebview

package main

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/ipc"
)

func TestHandleIPCMessageDispatchesChatMessageNotification(t *testing.T) {
	previous := showChatSessionNotificationFunc
	var gotTitle string
	var gotBody string
	var gotURL string
	showChatSessionNotificationFunc = func(title, body, chatURL string) {
		gotTitle = title
		gotBody = body
		gotURL = chatURL
	}
	t.Cleanup(func() { showChatSessionNotificationFunc = previous })

	payload, err := json.Marshal(chatMessagePayload{
		RoomID:  42,
		Subject: "Printer offline",
		Sender:  "Alex Tech",
		Message: "Please try printing again.",
	})
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}

	handleIPCMessage(ipc.Message{Type: "chat_message", Payload: payload})

	if gotTitle != "New MyPortal chat message" {
		t.Fatalf("title = %q", gotTitle)
	}
	for _, want := range []string{"Alex Tech", "Printer offline", "Please try printing again."} {
		if !strings.Contains(gotBody, want) {
			t.Fatalf("body = %q, want it to contain %q", gotBody, want)
		}
	}
	if gotURL == "" {
		t.Fatalf("chat notification action URL should not be empty")
	}
}
