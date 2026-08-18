package scanner

import (
	"encoding/base64"
	"encoding/binary"
	"testing"
	"unicode/utf16"
)

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
