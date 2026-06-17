//go:build nowebview

package main

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
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

func TestHandleIPCMessageDispatchesChatOpenNotificationAndLaunchesChatShell(t *testing.T) {
	previousNotify := showChatSessionNotificationFunc
	previousOpen := openChatWindowFunc
	previousPortalURL := gPortalURL
	gPortalURL = "https://portal.example.test"
	defer func() {
		showChatSessionNotificationFunc = previousNotify
		openChatWindowFunc = previousOpen
		gPortalURL = previousPortalURL
	}()

	var gotTitle string
	var gotBody string
	var gotActionURL string
	showChatSessionNotificationFunc = func(title, body, chatURL string) {
		gotTitle = title
		gotBody = body
		gotActionURL = chatURL
	}

	opened := make(chan string, 1)
	openChatWindowFunc = func(chatURL string, _ *api.ConfigResponse) {
		opened <- chatURL
	}

	payload, err := json.Marshal(chatOpenPayload{
		RoomID:      77,
		Subject:     "Laptop support",
		InitiatedBy: "Alex Tech",
		Message:     "I am starting a support chat.",
	})
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}

	handleIPCMessage(ipc.Message{Type: "chat_open", Payload: payload})

	select {
	case chatURL := <-opened:
		if !strings.Contains(chatURL, "/tray/chat?room=77") {
			t.Fatalf("opened chatURL = %q, want room-specific tray chat URL", chatURL)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("expected chat_open to launch the chat window automatically")
	}

	if gotTitle != "New MyPortal chat" {
		t.Fatalf("title = %q", gotTitle)
	}
	for _, want := range []string{"Alex Tech", "Laptop support", "I am starting a support chat."} {
		if !strings.Contains(gotBody, want) {
			t.Fatalf("body = %q, want it to contain %q", gotBody, want)
		}
	}
	if gotActionURL == "" {
		t.Fatalf("chat notification action URL should not be empty")
	}
}
