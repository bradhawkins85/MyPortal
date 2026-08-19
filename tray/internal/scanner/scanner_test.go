package scanner

import (
	"encoding/base64"
	"encoding/binary"
	"os"
	"strings"
	"testing"
	"unicode/utf16"
)

func TestWindowsScanScriptSupportsLegacyPowerShell(t *testing.T) {
	scriptBytes, err := os.ReadFile("scan_windows.ps1")
	if err != nil {
		t.Fatalf("read Windows scan script: %v", err)
	}
	script := string(scriptBytes)
	for _, unsupported := range []string{"::new()"} {
		if strings.Contains(script, unsupported) {
			t.Errorf("Windows scan script uses legacy-incompatible %q", unsupported)
		}
	}
	for _, fallback := range []string{"$legacyWindows = [Environment]::OSVersion.Version.Major -lt 10", "Win32_NetworkAdapterConfiguration", "arp.exe"} {
		if !strings.Contains(script, fallback) {
			t.Errorf("Windows scan script does not use compatibility fallback %q", fallback)
		}
	}
	for _, localMAC := range []string{"$localMacAddresses", "$adapter.MACAddress", "$localMacAddresses.ContainsKey($ip)"} {
		if !strings.Contains(script, localMAC) {
			t.Errorf("Windows scan script does not discover the scanner's own MAC with %q", localMAC)
		}
	}
	for _, modern := range []string{"Get-NetIPAddress -", "Get-NetNeighbor -"} {
		if !strings.Contains(script, modern) {
			t.Errorf("Windows scan script does not preserve modern command %q", modern)
		}
	}
}

func TestEncodePowerShellCommandUsesUTF16LE(t *testing.T) {
	script := "Write-Output '✓'\n"
	encoded, err := base64.StdEncoding.DecodeString(encodePowerShellCommand(script))
	if err != nil {
		t.Fatalf("decode encoded command: %v", err)
	}
	if len(encoded)%2 != 0 {
		t.Fatalf("encoded command has odd byte length %d", len(encoded))
	}
	codeUnits := make([]uint16, len(encoded)/2)
	for i := range codeUnits {
		codeUnits[i] = binary.LittleEndian.Uint16(encoded[i*2:])
	}
	if decoded := string(utf16.Decode(codeUnits)); decoded != script {
		t.Fatalf("decoded command = %q; want %q", decoded, script)
	}
}

func TestParseNetworkHostsJSONAcceptsEmptySuccessfulOutput(t *testing.T) {
	for _, output := range []string{"", " \r\n\t"} {
		hosts, err := parseNetworkHostsJSON([]byte(output))
		if err != nil {
			t.Fatalf("parseNetworkHostsJSON(%q): %v", output, err)
		}
		if hosts == nil || len(hosts) != 0 {
			t.Fatalf("parseNetworkHostsJSON(%q) = %#v; want non-nil empty slice", output, hosts)
		}
	}
}

func TestParseNetworkHostsJSONParsesHosts(t *testing.T) {
	hosts, err := parseNetworkHostsJSON([]byte(`[{"ip_address":"192.0.2.10","hostname":"printer"}]`))
	if err != nil {
		t.Fatalf("parseNetworkHostsJSON: %v", err)
	}
	if len(hosts) != 1 || hosts[0].IPAddress != "192.0.2.10" || hosts[0].Hostname != "printer" {
		t.Fatalf("parseNetworkHostsJSON returned %#v", hosts)
	}
}

func TestParseNetworkHostsJSONRejectsMalformedOutput(t *testing.T) {
	if _, err := parseNetworkHostsJSON([]byte(`[{`)); err == nil {
		t.Fatal("parseNetworkHostsJSON accepted malformed JSON")
	}
}

func TestNormalizeMACAddress(t *testing.T) {
	for input, want := range map[string]string{
		"8e59972a73d9":      "8E:59:97:2A:73:D9",
		"8e-59-97-2a-73-d9": "8E:59:97:2A:73:D9",
		"8e:59:97:2a:73:d9": "8E:59:97:2A:73:D9",
		"8e59.972a.73d9":    "8E:59:97:2A:73:D9",
	} {
		if got := normalizeMACAddress(input); got != want {
			t.Errorf("normalizeMACAddress(%q) = %q; want %q", input, got, want)
		}
	}
}
