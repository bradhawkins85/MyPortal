package defender

import (
	"errors"
	"testing"
)

func TestCommandScripts(t *testing.T) {
	tests := map[string]string{
		"quick_scan":       "Start-MpScan -ScanType QuickScan",
		"full_scan":        "Start-MpScan -ScanType FullScan",
		"signature_update": "Update-MpSignature",
		"enable_firewall":  "Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True",
	}
	for command, want := range tests {
		got, err := commandScript(command, "")
		if err != nil || got != want {
			t.Errorf("commandScript(%q) = %q, %v; want %q", command, got, err, want)
		}
	}
}

func TestThreatCommandRequiresDetectionIdentifier(t *testing.T) {
	got, err := commandScript("quarantine", "id'with-quote")
	if err != nil {
		t.Fatal(err)
	}
	if got != "Remove-MpThreat" {
		t.Fatalf("unexpected script: %s", got)
	}
	if _, err := commandScript("remediate", ""); err == nil {
		t.Fatal("expected missing detection identifier to be rejected")
	}
}

func TestUnknownCommandIsRejected(t *testing.T) {
	_, err := commandScript("format_disk", "")
	if !errors.Is(err, ErrUnsupportedCommand) {
		t.Fatalf("expected ErrUnsupportedCommand, got %v", err)
	}
}
