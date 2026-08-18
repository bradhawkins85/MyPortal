// Package scanner discovers hosts on locally connected IPv4 subnets.
package scanner

import (
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"net"
	"sort"
	"strings"
	"unicode/utf16"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

// encodePowerShellCommand returns the UTF-16LE/base64 representation required
// by powershell.exe -EncodedCommand. Passing the embedded script this way is
// reliable for a Windows service, whereas "-Command -" depends on PowerShell
// consuming and executing redirected standard input correctly.
func encodePowerShellCommand(script string) string {
	codeUnits := utf16.Encode([]rune(script))
	encoded := make([]byte, len(codeUnits)*2)
	for i, codeUnit := range codeUnits {
		binary.LittleEndian.PutUint16(encoded[i*2:], codeUnit)
	}
	return base64.StdEncoding.EncodeToString(encoded)
}

// parseNetworkHostsJSON treats an empty successful scanner response as an
// empty result set. Windows PowerShell emits no stdout when ConvertTo-Json is
// given an empty pipeline, which is a valid outcome when no hosts respond.
func parseNetworkHostsJSON(out []byte) ([]api.NetworkHost, error) {
	if len(strings.TrimSpace(string(out))) == 0 {
		return []api.NetworkHost{}, nil
	}
	var hosts []api.NetworkHost
	if err := json.Unmarshal(out, &hosts); err != nil {
		return nil, err
	}
	return hosts, nil
}

func connectedSubnets() []string {
	seen := map[string]bool{}
	var result []string
	addrs, _ := net.InterfaceAddrs()
	for _, addr := range addrs {
		ip, network, err := net.ParseCIDR(addr.String())
		if err != nil || ip.IsLoopback() || ip.To4() == nil {
			continue
		}
		ones, _ := network.Mask.Size()
		if ones < 16 {
			ones = 16
		} // bound accidental very large scans
		target := (&net.IPNet{IP: ip.Mask(net.CIDRMask(ones, 32)), Mask: net.CIDRMask(ones, 32)}).String()
		if !seen[target] {
			seen[target] = true
			result = append(result, target)
		}
	}
	sort.Strings(result)
	return result
}

// ConnectedSubnets returns the canonical CIDR targets used by network scans.
func ConnectedSubnets() []string {
	return connectedSubnets()
}
