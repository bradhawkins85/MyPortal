package main

import "testing"

func TestTRMMScriptSuccessMessageUsesAutomationScheduledNotice(t *testing.T) {
	msg := trmmScriptSuccessMessage("Nightly Maintenance", "")
	want := "The requested automation has been scheduled and will run in the background shortly."
	if msg != want {
		t.Fatalf("unexpected success message:\n got: %q\nwant: %q", msg, want)
	}
}

func TestTRMMScriptSuccessMessageUsesServerMessage(t *testing.T) {
	msg := trmmScriptSuccessMessage("Nightly Maintenance", "Tactical RMM script request submitted.")
	want := "Tactical RMM script request submitted."
	if msg != want {
		t.Fatalf("unexpected server success message:\n got: %q\nwant: %q", msg, want)
	}
}

func TestTRMMScriptSuccessMessageTrimsBlankLabel(t *testing.T) {
	msg := trmmScriptSuccessMessage("  \t", "")
	want := "The requested automation has been scheduled and will run in the background shortly."
	if msg != want {
		t.Fatalf("unexpected success message for blank label:\n got: %q\nwant: %q", msg, want)
	}
}
