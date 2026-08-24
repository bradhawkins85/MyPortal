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

// normalizeMACAddress produces the canonical representation used by MyPortal,
// including for the unseparated hexadecimal form returned by some arp.exe
// versions and localized Windows builds.
func normalizeMACAddress(address string) string {
	hex := strings.NewReplacer(":", "", "-", "", ".", "").Replace(strings.TrimSpace(address))
	if len(hex) != 12 {
		return strings.ToUpper(address)
	}
	parts := make([]string, 0, 6)
	for i := 0; i < len(hex); i += 2 {
		parts = append(parts, hex[i:i+2])
	}
	return strings.ToUpper(strings.Join(parts, ":"))
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

// AllowedTargets limits configured ranges to networks attached to this host.
// The narrower of two overlapping CIDRs is used, preventing configuration from
// turning a portable scanner into a route to unrelated networks.
func AllowedTargets(allowed []string) []string {
	connected := connectedSubnets()
	if len(allowed) == 0 {
		return connected
	}
	seen := map[string]bool{}
	var targets []string
	for _, raw := range allowed {
		_, permitted, err := net.ParseCIDR(raw)
		if err != nil || permitted.IP.To4() == nil {
			continue
		}
		for _, localRaw := range connected {
			_, local, _ := net.ParseCIDR(localRaw)
			if !permitted.Contains(local.IP) && !local.Contains(permitted.IP) {
				continue
			}
			permittedOnes, _ := permitted.Mask.Size()
			localOnes, _ := local.Mask.Size()
			target := permitted.String()
			if localOnes > permittedOnes {
				target = local.String()
			}
			if !seen[target] {
				seen[target] = true
				targets = append(targets, target)
			}
		}
	}
	sort.Strings(targets)
	return targets
}

// IPAllowed reports whether an address belongs to any configured WAN range.
func IPAllowed(address string, allowed []string) bool {
	if len(allowed) == 0 {
		return true
	}
	ip := net.ParseIP(address)
	for _, raw := range allowed {
		_, network, err := net.ParseCIDR(raw)
		if err == nil && network.Contains(ip) {
			return true
		}
	}
	return false
}
