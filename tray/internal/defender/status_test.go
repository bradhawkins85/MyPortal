package defender

import "testing"

func TestDecodeStatusAcceptsPowerShellUTF8BOM(t *testing.T) {
	output := append([]byte{0xef, 0xbb, 0xbf}, []byte(`{
        "antivirus_enabled": true,
        "realtime_protection_enabled": true,
        "tamper_protection_enabled": true,
        "firewall_domain_enabled": true,
        "firewall_private_enabled": false,
        "firewall_public_enabled": true,
        "signatures_updated_at": "2026-08-20T01:02:03Z",
        "scan_history": [{
            "scan_type": "quick",
            "started_at": "2026-08-20T01:00:00Z",
            "completed_at": "2026-08-20T01:02:00Z",
            "duration_seconds": 120,
            "status": "completed"
        }],
        "health_status": "healthy",
        "details": {"signature_version": "1.2.3"},
        "detections": [{
            "detection_uid": "det-123",
            "threat_name": "Test threat",
            "severity": "high",
            "status": "remediated",
            "detected_at": "2026-08-20T01:02:03Z",
            "infected_files": ["C:\\Users\\Example\\payload.exe"],
            "details": {"action_success": true}
        }]
    }`)...)

	status, err := decodeStatus(output)
	if err != nil {
		t.Fatalf("decodeStatus: %v", err)
	}
	if !status.AntivirusEnabled || status.HealthStatus != "healthy" {
		t.Fatalf("unexpected status: %+v", status)
	}
	if status.FirewallDomainEnabled == nil || !*status.FirewallDomainEnabled || status.FirewallPrivateEnabled == nil || *status.FirewallPrivateEnabled {
		t.Fatalf("unexpected firewall status: %+v", status)
	}
	if status.SignaturesUpdatedAt == nil {
		t.Fatal("expected signatures timestamp")
	}
	if len(status.ScanHistory) != 1 || status.ScanHistory[0].ScanType != "quick" {
		t.Fatalf("unexpected scan history: %+v", status.ScanHistory)
	}
	if len(status.Detections) != 1 || status.Detections[0].DetectionUID != "det-123" {
		t.Fatalf("unexpected detections: %+v", status.Detections)
	}
	if len(status.Detections[0].InfectedFiles) != 1 || status.Detections[0].InfectedFiles[0] != `C:\Users\Example\payload.exe` {
		t.Fatalf("unexpected infected files: %+v", status.Detections[0].InfectedFiles)
	}
}

func TestDecodeStatusRejectsPowerShellDiagnosticOutput(t *testing.T) {
	if _, err := decodeStatus([]byte("warning\n{\"health_status\":\"healthy\"}")); err == nil {
		t.Fatal("expected diagnostic output mixed into JSON to be rejected")
	}
}
