package main

import "testing"

func TestTRMMScriptSuccessMessageIncludesBackgroundNotice(t *testing.T) {
	msg := trmmScriptSuccessMessage("Nightly Maintenance")
	want := `The Tactical RMM script "Nightly Maintenance" has been executed and will run in the background shortly.`
	if msg != want {
		t.Fatalf("unexpected success message:\n got: %q\nwant: %q", msg, want)
	}
}

func TestTRMMScriptSuccessMessageTrimsBlankLabel(t *testing.T) {
	msg := trmmScriptSuccessMessage("  \t")
	want := "The Tactical RMM script has been executed and will run in the background shortly."
	if msg != want {
		t.Fatalf("unexpected success message for blank label:\n got: %q\nwant: %q", msg, want)
	}
}
