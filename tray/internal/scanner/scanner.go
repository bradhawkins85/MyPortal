// Package scanner discovers hosts on locally connected IPv4 subnets.
package scanner

import (
	"net"
	"sort"
)

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
