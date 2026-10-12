package inventory

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// number reads a JSON number, a numeric string or null.
type number float64

func (n *number) UnmarshalJSON(data []byte) error {
	text := strings.Trim(strings.TrimSpace(string(data)), `"`)
	if text == "" || text == "null" {
		*n = 0
		return nil
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		*n = 0
		return nil
	}
	*n = number(value)
	return nil
}

func (n number) uint64() uint64 {
	if n < 0 {
		return 0
	}
	return uint64(n)
}

// text reads a JSON string, number or null as text.
type text string

func (t *text) UnmarshalJSON(data []byte) error {
	var s string
	if err := json.Unmarshal(data, &s); err == nil {
		*t = text(s)
		return nil
	}
	raw := strings.TrimSpace(string(data))
	if raw == "null" {
		*t = ""
	} else {
		*t = text(raw)
	}
	return nil
}

// windowsReport is what windows.ps1 prints.
type windowsReport struct {
	Manufacturer   text   `json:"manufacturer"`
	Model          text   `json:"model"`
	Domain         text   `json:"domain"`
	Workgroup      text   `json:"workgroup"`
	RAMBytes       number `json:"ram_bytes"`
	Serial         text   `json:"serial"`
	BIOSVersion    text   `json:"bios_version"`
	OSName         text   `json:"os_name"`
	OSVersion      text   `json:"os_version"`
	DisplayVersion text   `json:"display_version"`
	Build          text   `json:"build"`
	UBR            text   `json:"ubr"`
	LastBoot       text   `json:"last_boot"`
	SystemDrive    text   `json:"system_drive"`
	CPUs           []*struct {
		Name    text   `json:"name"`
		Cores   number `json:"cores"`
		Logical number `json:"logical"`
	} `json:"cpus"`
	Memory []*struct {
		Slot         text   `json:"slot"`
		Size         number `json:"size"`
		Speed        number `json:"speed"`
		Manufacturer text   `json:"manufacturer"`
		Part         text   `json:"part"`
	} `json:"memory"`
	Volumes []*struct {
		Name  text   `json:"name"`
		Label text   `json:"label"`
		FS    text   `json:"fs"`
		Size  number `json:"size"`
		Free  number `json:"free"`
	} `json:"volumes"`
	Drives []*struct {
		Model  text   `json:"model"`
		Size   number `json:"size"`
		Media  text   `json:"media"`
		Bus    text   `json:"bus"`
		Serial text   `json:"serial"`
	} `json:"drives"`
	GPUs    []text `json:"gpus"`
	Network []*struct {
		Name      text   `json:"name"`
		MAC       text   `json:"mac"`
		Addresses []text `json:"addresses"`
	} `json:"network"`
	Software []*struct {
		Name        text `json:"name"`
		Version     text `json:"version"`
		Publisher   text `json:"publisher"`
		InstallDate text `json:"install_date"`
		Scope       text `json:"scope"`
	} `json:"software"`
	Errors []text `json:"errors"`
}

// applyWindows fills s from windows.ps1's output.
func applyWindows(s *Snapshot, output []byte) error {
	var r windowsReport
	if err := json.Unmarshal(output, &r); err != nil {
		return fmt.Errorf("could not read the PowerShell output: %w", err)
	}
	s.Manufacturer, s.Model, s.SerialNumber = string(r.Manufacturer), string(r.Model), string(r.Serial)
	s.BIOSVersion = string(r.BIOSVersion)
	s.Domain = string(r.Domain)
	s.RAMBytes = r.RAMBytes.uint64()
	s.OSName = string(r.OSName)
	// "23H2" reads better than "10.0.22631"; the build carries the detail.
	s.OSVersion = string(r.DisplayVersion)
	if s.OSVersion == "" {
		s.OSVersion = string(r.OSVersion)
	}
	s.OSBuild = string(r.Build)
	if r.Build != "" && r.UBR != "" {
		s.OSBuild = string(r.Build) + "." + string(r.UBR)
	}
	if boot, err := time.Parse(time.RFC3339Nano, string(r.LastBoot)); err == nil {
		boot = boot.UTC()
		s.LastBoot = &boot
	}
	for _, cpu := range r.CPUs {
		if cpu == nil {
			continue
		}
		if s.CPUModel == "" {
			s.CPUModel = string(cpu.Name)
		}
		s.CPUCores += int(cpu.Cores)
		s.CPULogical += int(cpu.Logical)
	}
	for _, m := range r.Memory {
		if m == nil || m.Size == 0 {
			continue
		}
		s.Memory = append(s.Memory, MemoryModule{Slot: string(m.Slot), SizeBytes: m.Size.uint64(), SpeedMHz: int(m.Speed),
			Manufacturer: clean(string(m.Manufacturer)), PartNumber: clean(string(m.Part))})
	}
	systemDrive := strings.ToUpper(string(r.SystemDrive))
	for _, v := range r.Volumes {
		if v == nil || v.Name == "" {
			continue
		}
		s.Disks = append(s.Disks, Disk{Name: string(v.Name), Label: string(v.Label), FileSystem: string(v.FS),
			SizeBytes: v.Size.uint64(), FreeBytes: v.Free.uint64(), System: strings.ToUpper(string(v.Name)) == systemDrive})
	}
	for _, d := range r.Drives {
		if d == nil || (d.Model == "" && d.Size == 0) {
			continue
		}
		media := string(d.Media)
		if media == "Unspecified" || media == "0" {
			media = ""
		}
		s.PhysicalDisks = append(s.PhysicalDisks, PhysicalDisk{Model: trim(string(d.Model)), SizeBytes: d.Size.uint64(),
			MediaType: media, BusType: string(d.Bus), Serial: trim(string(d.Serial))})
	}
	for _, gpu := range r.GPUs {
		if name := trim(string(gpu)); name != "" {
			s.GPUs = append(s.GPUs, name)
		}
	}
	for _, n := range r.Network {
		if n == nil || n.Name == "" {
			continue
		}
		adapter := NetworkAdapter{Name: string(n.Name), MAC: strings.ToLower(strings.ReplaceAll(string(n.MAC), "-", ":"))}
		for _, address := range n.Addresses {
			if address != "" && !strings.HasPrefix(strings.ToLower(string(address)), "fe80:") {
				adapter.Addresses = append(adapter.Addresses, string(address))
			}
		}
		s.Network = append(s.Network, adapter)
	}
	for _, p := range r.Software {
		if p == nil {
			continue
		}
		s.Software = append(s.Software, Program{Name: string(p.Name), Version: string(p.Version),
			Publisher: string(p.Publisher), InstallDate: installDate(string(p.InstallDate)), Source: string(p.Scope)})
	}
	for _, e := range r.Errors {
		if e != "" {
			s.Errors = append(s.Errors, string(e))
		}
	}
	return nil
}

// installDate turns the registry's yyyymmdd into yyyy-mm-dd; anything else is dropped.
func installDate(value string) string {
	value = strings.TrimSpace(value)
	if t, err := time.Parse("20060102", value); err == nil {
		return t.Format("2006-01-02")
	}
	return ""
}
