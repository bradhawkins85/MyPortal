// Package scanner discovers hosts on locally connected private subnets with Nmap.
package scanner

import (
	"encoding/xml"
	"fmt"
	"net"
	"os/exec"
	"sort"
	"strings"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

type nmapRun struct {
	Hosts []nmapHost `xml:"host"`
}
type nmapHost struct {
	Addresses []struct {
		Address string `xml:"addr,attr"`
		Type    string `xml:"addrtype,attr"`
		Vendor  string `xml:"vendor,attr"`
	} `xml:"address"`
	Hostnames []struct {
		Name string `xml:"name,attr"`
	} `xml:"hostnames>hostname"`
	Ports []struct {
		Port     int    `xml:"portid,attr"`
		Protocol string `xml:"protocol,attr"`
		State    struct {
			State string `xml:"state,attr"`
		} `xml:"state"`
		Service struct {
			Name string `xml:"name,attr"`
		} `xml:"service"`
	} `xml:"ports>port"`
	OSMatches []struct {
		Name string `xml:"name,attr"`
	} `xml:"os>osmatch"`
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

func Scan() ([]api.NetworkHost, error) {
	if _, err := exec.LookPath("nmap"); err != nil {
		if err = installNmap(); err != nil {
			return nil, err
		}
	}
	targets := connectedSubnets()
	if len(targets) == 0 {
		return nil, fmt.Errorf("no connected IPv4 subnet found")
	}
	args := append([]string{"-O", "--osscan-limit", "-oX", "-"}, targets...)
	out, err := exec.Command("nmap", args...).Output()
	if err != nil {
		return nil, fmt.Errorf("nmap: %w", err)
	}
	var run nmapRun
	if err := xml.Unmarshal(out, &run); err != nil {
		return nil, fmt.Errorf("parse nmap XML: %w", err)
	}
	hosts := make([]api.NetworkHost, 0, len(run.Hosts))
	for _, h := range run.Hosts {
		var item api.NetworkHost
		for _, a := range h.Addresses {
			if a.Type == "ipv4" {
				item.IPAddress = a.Address
			}
			if a.Type == "mac" {
				item.MACAddress = strings.ToUpper(strings.ReplaceAll(a.Address, "-", ":"))
				item.Vendor = a.Vendor
			}
		}
		if len(h.Hostnames) > 0 {
			item.Hostname = h.Hostnames[0].Name
		}
		if len(h.OSMatches) > 0 {
			item.OSDetails = h.OSMatches[0].Name
		}
		var ports []string
		for _, p := range h.Ports {
			if p.State.State == "open" {
				ports = append(ports, fmt.Sprintf("%d/%s %s", p.Port, p.Protocol, p.Service.Name))
			}
		}
		item.OpenPorts = strings.Join(ports, ", ")
		if item.IPAddress != "" {
			hosts = append(hosts, item)
		}
	}
	return hosts, nil
}
