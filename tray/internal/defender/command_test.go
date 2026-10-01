package defender

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func TestCommandScripts(t *testing.T) {
	tests := map[string]string{
		"quick_scan":       "Start-MpScan -ScanType QuickScan",
		"full_scan":        "Start-MpScan -ScanType FullScan",
		"signature_update": "Update-MpSignature",
	}
	for command, want := range tests {
		got, err := commandScript(command, "")
		if err != nil || got != want {
			t.Errorf("commandScript(%q) = %q, %v; want %q", command, got, err, want)
		}
	}
}

func TestEnableFirewallVerifiesTheActiveProfiles(t *testing.T) {
	got, err := commandScript("enable_firewall", "")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"Set-NetFirewallProfile -Profile Domain,Private,Public -Enabled True", "-PolicyStore ActiveStore", "throw $message"} {
		if !strings.Contains(got, want) {
			t.Errorf("enable_firewall script is missing %q", want)
		}
	}
}

func TestFullScanTimeoutOutlastsQuickScan(t *testing.T) {
	if CommandTimeout("full_scan") <= CommandTimeout("quick_scan") || CommandTimeout("full_scan") >= 24*time.Hour {
		t.Fatal("full scans need longer than quick scans but must finish before the portal expires the command")
	}
	if !IsScan("full_scan") || IsScan("signature_update") {
		t.Fatal("unexpected scan classification")
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
