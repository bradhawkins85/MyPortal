//go:build nowebview

package main

import (
	"encoding/json"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/ipc"
)

func resetAnnouncedChatRooms(t *testing.T) {
	t.Helper()
	announcedChatRoomsMu.Lock()
	announcedChatRooms = map[int]bool{}
	announcedChatRoomsMu.Unlock()
	t.Cleanup(func() {
		announcedChatRoomsMu.Lock()
		announcedChatRooms = map[int]bool{}
		announcedChatRoomsMu.Unlock()
	})
}

func TestHandleIPCMessageAnnouncesFirstChatMessageOnly(t *testing.T) {
	resetAnnouncedChatRooms(t)
	previousNotify := showChatSessionNotificationFunc
	previousOpen := openChatWindowFunc
	previousRequest := requestChatTokenFunc
	previousPortalURL := gPortalURL
	gPortalURL = "https://portal.example.test"
	var notifyMu sync.Mutex
	var titles, bodies []string
	showChatSessionNotificationFunc = func(title, body, chatURL string) {
		notifyMu.Lock()
		defer notifyMu.Unlock()
		titles = append(titles, title)
		bodies = append(bodies, body)
	}
	requestChatTokenFunc = func(roomID int) string {
		return gPortalURL + "/tray/chat?token=test-token&room=" + itoa(roomID)
	}
	opened := make(chan string, 2)
	openChatWindowFunc = func(chatURL string, _ *api.ConfigResponse) {
		opened <- chatURL
	}
	t.Cleanup(func() {
		showChatSessionNotificationFunc = previousNotify
		openChatWindowFunc = previousOpen
		requestChatTokenFunc = previousRequest
		gPortalURL = previousPortalURL
	})

	payload, err := json.Marshal(chatMessagePayload{
		RoomID:  42,
		Subject: "Printer offline",
		Sender:  "Alex Tech",
		Message: "Please try printing again.",
	})
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}

	for i := 0; i < 2; i++ {
		handleIPCMessage(ipc.Message{Type: "chat_message", Payload: payload})
		select {
		case chatURL := <-opened:
			if !strings.Contains(chatURL, "/tray/chat?token=test-token&room=42") {
				t.Fatalf("opened chatURL = %q, want room-specific authenticated tray chat URL", chatURL)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("expected chat_message to launch the chat window automatically")
		}
	}

	notifyMu.Lock()
	defer notifyMu.Unlock()
	if len(titles) != 1 {
		t.Fatalf("notifications = %d, want only the first message to notify", len(titles))
	}
	for _, want := range []string{"Alex Tech", "Printer offline", "Please try printing again."} {
		if !strings.Contains(bodies[0], want) {
			t.Fatalf("body = %q, want it to contain %q", bodies[0], want)
		}
	}
}

func TestHandleIPCMessageChatOpenWithoutMessageWaitsForFirstMessage(t *testing.T) {
	resetAnnouncedChatRooms(t)
	previousNotify := showChatSessionNotificationFunc
	previousOpen := openChatWindowFunc
	previousRequest := requestChatTokenFunc
	t.Cleanup(func() {
		showChatSessionNotificationFunc = previousNotify
		openChatWindowFunc = previousOpen
		requestChatTokenFunc = previousRequest
	})

	var notifyMu sync.Mutex
	notified := 0
	showChatSessionNotificationFunc = func(title, body, chatURL string) {
		notifyMu.Lock()
		notified++
		notifyMu.Unlock()
	}
	requestChatTokenFunc = func(roomID int) string {
		return "https://portal.example.test/tray/chat?token=test-token&room=" + itoa(roomID)
	}
	opened := make(chan string, 2)
	openChatWindowFunc = func(chatURL string, _ *api.ConfigResponse) {
		opened <- chatURL
	}

	openPayload, err := json.Marshal(chatOpenPayload{RoomID: 26429, Subject: "test", InitiatedBy: "Alex Tech"})
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	handleIPCMessage(ipc.Message{Type: "chat_open", Payload: openPayload})

	select {
	case chatURL := <-opened:
		t.Fatalf("chat window launched with %q before the technician sent a message", chatURL)
	case <-time.After(150 * time.Millisecond):
	}
	notifyMu.Lock()
	if notified != 0 {
		t.Fatalf("notifications = %d, want none before the first message", notified)
	}
	notifyMu.Unlock()

	messagePayload, err := json.Marshal(chatMessagePayload{RoomID: 26429, Sender: "Alex Tech", Message: "Hello"})
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	handleIPCMessage(ipc.Message{Type: "chat_message", Payload: messagePayload})

	select {
	case <-opened:
	case <-time.After(2 * time.Second):
		t.Fatal("expected the first message to launch the chat window")
	}
	notifyMu.Lock()
	defer notifyMu.Unlock()
	if notified != 1 {
		t.Fatalf("notifications = %d, want one for the first message", notified)
	}
}

func TestHandleIPCMessageDoesNotLaunchChatWhenRoomTokenRejected(t *testing.T) {
	resetAnnouncedChatRooms(t)
	previousNotify := showChatSessionNotificationFunc
	previousOpen := openChatWindowFunc
	previousRequest := requestChatTokenFunc
	defer func() {
		showChatSessionNotificationFunc = previousNotify
		openChatWindowFunc = previousOpen
		requestChatTokenFunc = previousRequest
	}()

	showChatSessionNotificationFunc = func(title, body, chatURL string) {}
	requestChatTokenFunc = func(roomID int) string { return "" }
	opened := make(chan string, 1)
	openChatWindowFunc = func(chatURL string, _ *api.ConfigResponse) {
		opened <- chatURL
	}

	payload, err := json.Marshal(chatMessagePayload{RoomID: 42})
	if err != nil {
		t.Fatalf("marshal payload: %v", err)
	}
	handleIPCMessage(ipc.Message{Type: "chat_message", Payload: payload})

	select {
	case chatURL := <-opened:
		t.Fatalf("chat window launched with %q after room token was rejected", chatURL)
	case <-time.After(150 * time.Millisecond):
		// Expected: closed/stale rooms do not launch the chat shell.
	}
}

func TestHandleIPCMessageDispatchesChatOpenNotificationAndLaunchesChatShell(t *testing.T) {
	resetAnnouncedChatRooms(t)
	previousNotify := showChatSessionNotificationFunc
	previousOpen := openChatWindowFunc
	previousRequest := requestChatTokenFunc
	previousPortalURL := gPortalURL
	gPortalURL = "https://portal.example.test"
	defer func() {
		showChatSessionNotificationFunc = previousNotify
		openChatWindowFunc = previousOpen
		requestChatTokenFunc = previousRequest
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

	requestChatTokenFunc = func(roomID int) string {
		return gPortalURL + "/tray/chat?token=test-token&room=" + itoa(roomID)
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
		if !strings.Contains(chatURL, "/tray/chat?token=test-token&room=77") {
			t.Fatalf("opened chatURL = %q, want room-specific authenticated tray chat URL", chatURL)
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
