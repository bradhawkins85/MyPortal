package checks

import (
	"context"
	"encoding/hex"
	"fmt"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/gosnmp/gosnmp"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

// snmpTimeout is how long one SNMP request waits for an answer before its one
// retry; a device that never answers costs at most about twice this.
var snmpTimeout = 3 * time.Second

// snmpMaxOIDs is how many OIDs go in one GET request.
const snmpMaxOIDs = 20

// PollSNMP asks one network device for each of the target's OIDs. Every OID
// gets a result: its value, or why it could not be read.
func PollSNMP(ctx context.Context, target client.SNMPTarget) []client.SNMPResult {
	port := target.Port
	if port <= 0 || port > 65535 {
		port = 161
	}
	results := make([]client.SNMPResult, 0, len(target.OIDs))
	fail := func(oids []string, err error) {
		for _, oid := range oids {
			results = append(results, client.SNMPResult{Host: target.Host, Port: port, OID: oid, Error: err.Error()})
		}
	}
	session, err := snmpSession(ctx, target, port)
	if err != nil {
		fail(target.OIDs, err)
		return results
	}
	if err := session.Connect(); err != nil {
		fail(target.OIDs, fmt.Errorf("could not connect: %w", err))
		return results
	}
	defer session.Conn.Close()
	for start := 0; start < len(target.OIDs); start += snmpMaxOIDs {
		end := min(start+snmpMaxOIDs, len(target.OIDs))
		batch := target.OIDs[start:end]
		packet, err := session.Get(dotted(batch))
		if err != nil {
			fail(batch, err)
			continue
		}
		if packet.Error != gosnmp.NoError {
			fail(batch, fmt.Errorf("the device answered with an error: %s", packet.Error))
			continue
		}
		byOID := make(map[string]gosnmp.SnmpPDU, len(packet.Variables))
		for _, variable := range packet.Variables {
			byOID[strings.TrimPrefix(variable.Name, ".")] = variable
		}
		for _, oid := range batch {
			variable, ok := byOID[strings.TrimPrefix(oid, ".")]
			if !ok {
				fail([]string{oid}, fmt.Errorf("the device did not return this OID"))
				continue
			}
			value, kind, readErr := FormatSNMPValue(variable)
			result := client.SNMPResult{Host: target.Host, Port: port, OID: oid, Value: value, Type: kind}
			if readErr != nil {
				result.Error = readErr.Error()
			}
			results = append(results, result)
		}
	}
	return results
}

func dotted(oids []string) []string {
	out := make([]string, len(oids))
	for i, oid := range oids {
		out[i] = "." + strings.TrimPrefix(oid, ".")
	}
	return out
}

// snmpSession builds the request settings for the target's version and, for
// SNMP v3, its user-based security.
func snmpSession(ctx context.Context, target client.SNMPTarget, port int) (*gosnmp.GoSNMP, error) {
	session := &gosnmp.GoSNMP{
		Context:            ctx,
		Target:             target.Host,
		Port:               uint16(port),
		Transport:          "udp",
		Timeout:            snmpTimeout,
		Retries:            1,
		ExponentialTimeout: false,
		MaxOids:            snmpMaxOIDs,
	}
	switch target.Version {
	case "v1":
		session.Version = gosnmp.Version1
		session.Community = target.Community
	case "", "v2c":
		session.Version = gosnmp.Version2c
		session.Community = target.Community
	case "v3":
		session.Version = gosnmp.Version3
		session.SecurityModel = gosnmp.UserSecurityModel
		params := &gosnmp.UsmSecurityParameters{UserName: target.Username}
		switch target.SecurityLevel {
		case "noAuthNoPriv":
			session.MsgFlags = gosnmp.NoAuthNoPriv
		case "authNoPriv":
			session.MsgFlags = gosnmp.AuthNoPriv
		case "", "authPriv":
			session.MsgFlags = gosnmp.AuthPriv
		default:
			return nil, fmt.Errorf("unknown SNMP v3 security level %q", target.SecurityLevel)
		}
		if session.MsgFlags != gosnmp.NoAuthNoPriv {
			auth, err := authProtocol(target.AuthProtocol)
			if err != nil {
				return nil, err
			}
			params.AuthenticationProtocol = auth
			params.AuthenticationPassphrase = target.AuthPassword
		}
		if session.MsgFlags == gosnmp.AuthPriv {
			priv, err := privProtocol(target.PrivProtocol)
			if err != nil {
				return nil, err
			}
			params.PrivacyProtocol = priv
			params.PrivacyPassphrase = target.PrivPassword
		}
		session.SecurityParameters = params
	default:
		return nil, fmt.Errorf("unknown SNMP version %q", target.Version)
	}
	return session, nil
}

func authProtocol(name string) (gosnmp.SnmpV3AuthProtocol, error) {
	switch strings.ToUpper(name) {
	case "", "SHA":
		return gosnmp.SHA, nil
	case "SHA256":
		return gosnmp.SHA256, nil
	case "SHA512":
		return gosnmp.SHA512, nil
	case "MD5":
		return gosnmp.MD5, nil
	}
	return gosnmp.NoAuth, fmt.Errorf("unknown SNMP v3 authentication protocol %q", name)
}

func privProtocol(name string) (gosnmp.SnmpV3PrivProtocol, error) {
	switch strings.ToUpper(name) {
	case "", "AES":
		return gosnmp.AES, nil
	case "AES256":
		return gosnmp.AES256, nil
	case "DES":
		return gosnmp.DES, nil
	}
	return gosnmp.NoPriv, fmt.Errorf("unknown SNMP v3 encryption protocol %q", name)
}

// FormatSNMPValue turns a returned variable into text MyPortal can compare:
// numbers in decimal, readable strings as they are and other bytes as hex.
func FormatSNMPValue(variable gosnmp.SnmpPDU) (string, string, error) {
	switch variable.Type {
	case gosnmp.NoSuchObject, gosnmp.NoSuchInstance:
		return "", "", fmt.Errorf("the device has no such OID")
	case gosnmp.EndOfMibView:
		return "", "", fmt.Errorf("the OID is past the end of the device's MIB")
	case gosnmp.Null:
		return "", "null", nil
	case gosnmp.Integer, gosnmp.Counter32, gosnmp.Gauge32, gosnmp.TimeTicks, gosnmp.Counter64, gosnmp.Uinteger32:
		return gosnmp.ToBigInt(variable.Value).String(), "number", nil
	case gosnmp.OctetString:
		raw, _ := variable.Value.([]byte)
		if printable(raw) {
			return strings.TrimRight(string(raw), "\x00"), "string", nil
		}
		return hex.EncodeToString(raw), "hex", nil
	case gosnmp.ObjectIdentifier:
		return strings.TrimPrefix(fmt.Sprint(variable.Value), "."), "oid", nil
	case gosnmp.IPAddress:
		return fmt.Sprint(variable.Value), "ip", nil
	}
	return fmt.Sprint(variable.Value), "other", nil
}

func printable(raw []byte) bool {
	if !utf8.Valid(raw) {
		return false
	}
	for _, r := range strings.TrimRight(string(raw), "\x00") {
		if !unicode.IsPrint(r) && !unicode.IsSpace(r) {
			return false
		}
	}
	return true
}
