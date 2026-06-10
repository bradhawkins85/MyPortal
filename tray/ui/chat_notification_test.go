package main

import (
	"strings"
	"testing"
)

func TestChatNotificationTextIncludesInitiatorSubjectAndMessageSummary(t *testing.T) {
	longMessage := strings.Repeat("hello ", 50)
	title, body := chatNotificationText(chatOpenPayload{
		Subject:     "Printer issue",
		InitiatedBy: "Alex Tech",
		Message:     longMessage,
	})

	if title != "New MyPortal chat" {
		t.Fatalf("title = %q", title)
	}
	for _, want := range []string{"Alex Tech", "Printer issue", "hello"} {
		if !strings.Contains(body, want) {
			t.Fatalf("body = %q, want it to contain %q", body, want)
		}
	}
	if len([]rune(body)) > 260 {
		t.Fatalf("body = %q, expected concise notification summary", body)
	}
	if !strings.Contains(body, "…") {
		t.Fatalf("body = %q, expected truncated message ellipsis", body)
	}
}

func TestChatNotificationTextFallsBackToTechnician(t *testing.T) {
	_, body := chatNotificationText(chatOpenPayload{})
	if !strings.Contains(body, "A technician started a support chat") {
		t.Fatalf("body = %q, want fallback initiator", body)
	}
}
