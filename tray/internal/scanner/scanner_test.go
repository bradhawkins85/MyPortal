package scanner

import "testing"

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
