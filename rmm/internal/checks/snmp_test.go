package checks

import (
	"context"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/gosnmp/gosnmp"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func TestFormatSNMPValue(t *testing.T) {
	cases := []struct {
		pdu   gosnmp.SnmpPDU
		value string
		kind  string
		err   bool
	}{
		{gosnmp.SnmpPDU{Type: gosnmp.Integer, Value: 42}, "42", "number", false},
		{gosnmp.SnmpPDU{Type: gosnmp.TimeTicks, Value: uint32(123456)}, "123456", "number", false},
		{gosnmp.SnmpPDU{Type: gosnmp.Counter64, Value: uint64(18446744073709551615)}, "18446744073709551615", "number", false},
		{gosnmp.SnmpPDU{Type: gosnmp.OctetString, Value: []byte("Core switch\x00")}, "Core switch", "string", false},
		{gosnmp.SnmpPDU{Type: gosnmp.OctetString, Value: []byte{0x00, 0x1a, 0xff}}, "001aff", "hex", false},
		{gosnmp.SnmpPDU{Type: gosnmp.ObjectIdentifier, Value: ".1.3.6.1.4.1.9"}, "1.3.6.1.4.1.9", "oid", false},
		{gosnmp.SnmpPDU{Type: gosnmp.NoSuchInstance}, "", "", true},
		{gosnmp.SnmpPDU{Type: gosnmp.EndOfMibView}, "", "", true},
	}
	for _, c := range cases {
		value, kind, err := FormatSNMPValue(c.pdu)
		if value != c.value || kind != c.kind || (err != nil) != c.err {
			t.Errorf("FormatSNMPValue(%v) = %q, %q, %v; want %q, %q, error %v", c.pdu, value, kind, err, c.value, c.kind, c.err)
		}
	}
}

func TestSNMPSessionVersions(t *testing.T) {
	session, err := snmpSession(context.Background(), client.SNMPTarget{Host: "h", Version: "v1", Community: "c"}, 161)
	if err != nil || session.Version != gosnmp.Version1 || session.Community != "c" {
		t.Fatalf("v1 session = %+v, %v", session, err)
	}
	session, err = snmpSession(context.Background(), client.SNMPTarget{
		Host: "h", Version: "v3", Username: "mon", SecurityLevel: "authPriv",
		AuthProtocol: "SHA256", AuthPassword: "authpass1", PrivProtocol: "AES256", PrivPassword: "privpass1",
	}, 1161)
	if err != nil {
		t.Fatal(err)
	}
	params := session.SecurityParameters.(*gosnmp.UsmSecurityParameters)
	if session.MsgFlags != gosnmp.AuthPriv || params.AuthenticationProtocol != gosnmp.SHA256 ||
		params.PrivacyProtocol != gosnmp.AES256 || params.UserName != "mon" || session.Port != 1161 {
		t.Fatalf("v3 session = %+v %+v", session, params)
	}
	if _, err := snmpSession(context.Background(), client.SNMPTarget{Version: "v9"}, 161); err == nil {
		t.Fatal("unknown version accepted")
	}
	if _, err := snmpSession(context.Background(), client.SNMPTarget{Version: "v3", SecurityLevel: "authNoPriv", AuthProtocol: "CRC"}, 161); err == nil {
		t.Fatal("unknown auth protocol accepted")
	}
}

// A device that never answers gives every OID an error rather than no result.
func TestPollSNMPReportsEveryOIDWhenNothingAnswers(t *testing.T) {
	listener, err := net.ListenPacket("udp", "127.0.0.1:0")
	if err != nil {
		t.Skip("no UDP socket:", err)
	}
	defer listener.Close()
	previous := snmpTimeout
	snmpTimeout = 100 * time.Millisecond
	defer func() { snmpTimeout = previous }()
	port := listener.LocalAddr().(*net.UDPAddr).Port
	results := PollSNMP(context.Background(), client.SNMPTarget{
		Host: "127.0.0.1", Port: port, Version: "v2c", Community: "public",
		OIDs: []string{"1.3.6.1.2.1.1.3.0", "1.3.6.1.2.1.1.5.0"},
	})
	if len(results) != 2 {
		t.Fatalf("got %d results, want 2", len(results))
	}
	for _, result := range results {
		if result.Error == "" || result.Port != port || result.Host != "127.0.0.1" {
			t.Errorf("result = %+v, want an error for the silent device", result)
		}
		if strings.Contains(result.Error, "public") {
			t.Errorf("error leaks the community string: %q", result.Error)
		}
	}
}
