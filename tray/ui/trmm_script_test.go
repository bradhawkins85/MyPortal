package main

import "testing"

func TestTRMMScriptSuccessMessageIncludesRequestedScriptNotice(t *testing.T) {
	msg := trmmScriptSuccessMessage("Nightly Maintenance")
	want := `The script "Nightly Maintenance" you requested has been executed and will run shortly.`
	if msg != want {
		t.Fatalf("unexpected success message:\n got: %q\nwant: %q", msg, want)
	}
}

func TestTRMMScriptSuccessMessageTrimsBlankLabel(t *testing.T) {
	msg := trmmScriptSuccessMessage("  \t")
	want := "Your requested script has been executed and will run shortly."
	if msg != want {
		t.Fatalf("unexpected success message for blank label:\n got: %q\nwant: %q", msg, want)
	}
}
