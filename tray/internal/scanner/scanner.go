// Package scanner discovers hosts on locally connected IPv4 subnets.
package scanner

import (
	"encoding/json"
	"net"
	"sort"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

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
