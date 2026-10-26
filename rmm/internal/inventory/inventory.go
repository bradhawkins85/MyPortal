// Package inventory collects a snapshot of the device's hardware and
// installed software for MyPortal (POST /api/rmm/agent/inventory).
//
// Each platform fills what it can (collect_windows.go, collect_linux.go,
// collect_darwin.go); a part that fails is skipped and named in Errors, so a
// broken WMI class or missing package manager still sends the rest.
package inventory

import (
	"context"
	"net"
	"os"
	"runtime"
	"sort"
	"strings"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/proxmox"
)

// Limits keep one snapshot small enough for a single request.
const (
	MaxSoftware = 5000
	maxText     = 255
	maxList     = 64
)

// Disk is one mounted volume (a Windows drive letter, or a Unix mount point).
type Disk struct {
	Name       string `json:"name"`
	Label      string `json:"label,omitempty"`
	FileSystem string `json:"file_system,omitempty"`
	SizeBytes  uint64 `json:"size_bytes"`
	FreeBytes  uint64 `json:"free_bytes"`
	System     bool   `json:"system,omitempty"`
}

// PhysicalDisk is a drive as the hardware reports it.
type PhysicalDisk struct {
	Model     string `json:"model,omitempty"`
	SizeBytes uint64 `json:"size_bytes,omitempty"`
	MediaType string `json:"media_type,omitempty"`
	BusType   string `json:"bus_type,omitempty"`
	Serial    string `json:"serial,omitempty"`
}

// MemoryModule is one installed RAM stick.
type MemoryModule struct {
	Slot         string `json:"slot,omitempty"`
	SizeBytes    uint64 `json:"size_bytes,omitempty"`
	SpeedMHz     int    `json:"speed_mhz,omitempty"`
	Manufacturer string `json:"manufacturer,omitempty"`
	PartNumber   string `json:"part_number,omitempty"`
}

// NetworkAdapter is an interface that is up and has an address.
type NetworkAdapter struct {
	Name      string   `json:"name"`
	MAC       string   `json:"mac,omitempty"`
	Addresses []string `json:"addresses,omitempty"`
}

// Program is one installed application or package.
type Program struct {
	Name        string `json:"name"`
	Version     string `json:"version,omitempty"`
	Publisher   string `json:"publisher,omitempty"`
	InstallDate string `json:"install_date,omitempty"`
	Source      string `json:"source,omitempty"`
}

// Snapshot is everything sent to MyPortal.
type Snapshot struct {
	CollectedAt   time.Time        `json:"collected_at"`
	Hostname      string           `json:"hostname,omitempty"`
	Manufacturer  string           `json:"manufacturer,omitempty"`
	Model         string           `json:"model,omitempty"`
	SerialNumber  string           `json:"serial_number,omitempty"`
	BIOSVersion   string           `json:"bios_version,omitempty"`
	OSName        string           `json:"os_name,omitempty"`
	OSVersion     string           `json:"os_version,omitempty"`
	OSBuild       string           `json:"os_build,omitempty"`
	Arch          string           `json:"arch,omitempty"`
	Domain        string           `json:"domain,omitempty"`
	CPUModel      string           `json:"cpu_model,omitempty"`
	CPUCores      int              `json:"cpu_cores,omitempty"`
	CPULogical    int              `json:"cpu_logical_processors,omitempty"`
	RAMBytes      uint64           `json:"ram_bytes,omitempty"`
	LastBoot      *time.Time       `json:"last_boot_at,omitempty"`
	Disks         []Disk           `json:"disks"`
	PhysicalDisks []PhysicalDisk   `json:"physical_disks"`
	Memory        []MemoryModule   `json:"memory_modules"`
	GPUs          []string         `json:"gpus"`
	Network       []NetworkAdapter `json:"network_adapters"`
	Software      []Program        `json:"software"`
	Proxmox       *proxmox.Status  `json:"proxmox,omitempty"`
	Errors        []string         `json:"errors"`
}

func (s *Snapshot) fail(part string, err error) {
	if err != nil {
		s.Errors = append(s.Errors, part+": "+err.Error())
	}
}

// Collect gathers the snapshot. It never fails outright: parts it could not
// read are listed in Errors.
func Collect(ctx context.Context) Snapshot {
	s := Snapshot{CollectedAt: time.Now().UTC(), Arch: runtime.GOARCH}
	s.Hostname, _ = os.Hostname()
	collectPlatform(ctx, &s)
	if len(s.Network) == 0 {
		s.Network = networkAdapters()
	}
	return Normalise(s)
}

// Normalise trims text, drops empty and duplicate programs, sorts lists and
// applies the size limits.
func Normalise(s Snapshot) Snapshot {
	for _, field := range []*string{&s.Hostname, &s.Manufacturer, &s.Model, &s.SerialNumber, &s.BIOSVersion,
		&s.OSName, &s.OSVersion, &s.OSBuild, &s.Domain, &s.CPUModel} {
		*field = clean(*field)
	}
	seen := map[string]bool{}
	programs := make([]Program, 0, len(s.Software))
	for _, p := range s.Software {
		p.Name, p.Version, p.Publisher = trim(p.Name), trim(p.Version), trim(p.Publisher)
		key := strings.ToLower(p.Name + "\x00" + p.Version)
		if p.Name == "" || seen[key] {
			continue
		}
		seen[key] = true
		programs = append(programs, p)
	}
	sort.Slice(programs, func(i, j int) bool {
		return strings.ToLower(programs[i].Name) < strings.ToLower(programs[j].Name)
	})
	if len(programs) > MaxSoftware {
		s.Errors = append(s.Errors, "software: list cut to the first 5000 programs")
		programs = programs[:MaxSoftware]
	}
	s.Software = programs
	sort.Slice(s.Disks, func(i, j int) bool { return s.Disks[i].Name < s.Disks[j].Name })
	s.Disks = capList(s.Disks)
	s.PhysicalDisks = capList(s.PhysicalDisks)
	s.Memory = capList(s.Memory)
	s.GPUs = capList(s.GPUs)
	s.Network = capList(s.Network)
	s.Errors = capList(s.Errors)
	return s
}

func capList[T any](items []T) []T {
	if items == nil {
		return []T{}
	}
	if len(items) > maxList {
		return items[:maxList]
	}
	return items
}

// clean trims a hardware field and drops firmware filler text.
func clean(value string) string {
	value = trim(value)
	if placeholder(value) {
		return ""
	}
	return value
}

func trim(value string) string {
	value = strings.TrimSpace(strings.Trim(value, "\x00"))
	if len(value) > maxText {
		value = strings.ToValidUTF8(value[:maxText], "")
	}
	return value
}

// placeholder is true for the filler text firmware leaves in empty fields.
func placeholder(value string) bool {
	switch strings.ToLower(value) {
	case "to be filled by o.e.m.", "to be filled by oem", "default string", "system product name",
		"system manufacturer", "not specified", "not applicable", "none", "n/a", "0", "unknown":
		return true
	}
	return false
}

// networkAdapters lists interfaces that are up, are not loopback, and have
// an address.
func networkAdapters() []NetworkAdapter {
	interfaces, err := net.Interfaces()
	if err != nil {
		return nil
	}
	var adapters []NetworkAdapter
	for _, iface := range interfaces {
		if iface.Flags&net.FlagUp == 0 || iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, _ := iface.Addrs()
		var addresses []string
		for _, addr := range addrs {
			if ipnet, ok := addr.(*net.IPNet); ok && !ipnet.IP.IsLinkLocalUnicast() {
				addresses = append(addresses, ipnet.IP.String())
			}
		}
		if len(addresses) == 0 {
			continue
		}
		adapters = append(adapters, NetworkAdapter{Name: iface.Name, MAC: iface.HardwareAddr.String(), Addresses: addresses})
	}
	return adapters
}
