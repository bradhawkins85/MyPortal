package defender

import "testing"

func TestDecodeStatusAcceptsPowerShellUTF8BOM(t *testing.T) {
	output := append([]byte{0xef, 0xbb, 0xbf}, []byte(`{
        "antivirus_enabled": true,
        "realtime_protection_enabled": true,
        "tamper_protection_enabled": true,
        "signatures_updated_at": "2026-08-20T01:02:03Z",
        "scan_history": [{
            "scan_type": "quick",
            "started_at": "2026-08-20T01:00:00Z",
            "completed_at": "2026-08-20T01:02:00Z",
            "duration_seconds": 120,
            "status": "completed"
        }],
        "health_status": "healthy",
        "details": {"signature_version": "1.2.3"}
    }`)...)

	status, err := decodeStatus(output)
	if err != nil {
		t.Fatalf("decodeStatus: %v", err)
	}
	if !status.AntivirusEnabled || status.HealthStatus != "healthy" {
		t.Fatalf("unexpected status: %+v", status)
	}
	if status.SignaturesUpdatedAt == nil {
		t.Fatal("expected signatures timestamp")
	}
	if len(status.ScanHistory) != 1 || status.ScanHistory[0].ScanType != "quick" {
		t.Fatalf("unexpected scan history: %+v", status.ScanHistory)
	}
}

func TestDecodeStatusRejectsPowerShellDiagnosticOutput(t *testing.T) {
	if _, err := decodeStatus([]byte("warning\n{\"health_status\":\"healthy\"}")); err == nil {
		t.Fatal("expected diagnostic output mixed into JSON to be rejected")
	}
}
