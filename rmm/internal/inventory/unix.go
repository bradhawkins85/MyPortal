package inventory

import (
	"bufio"
	"encoding/json"
	"strconv"
	"strings"
	"time"
)

// parseOSRelease reads /etc/os-release.
func parseOSRelease(data string) map[string]string {
	values := map[string]string{}
	for _, line := range strings.Split(data, "\n") {
		key, value, ok := strings.Cut(strings.TrimSpace(line), "=")
		if !ok || strings.HasPrefix(key, "#") {
			continue
		}
		values[key] = strings.Trim(value, `"'`)
	}
	return values
}

// parseCPUInfo reads /proc/cpuinfo: the model, physical cores and logical
// processors.
func parseCPUInfo(data string) (model string, cores, logical int) {
	type core struct{ socket, id string }
	unique := map[core]bool{}
	var socket, coreID string
	coresPerSocket := map[string]int{}
	flush := func() {
		if coreID != "" {
			unique[core{socket, coreID}] = true
		}
		socket, coreID = "", ""
	}
	scanner := bufio.NewScanner(strings.NewReader(data))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.TrimSpace(line) == "" {
			flush()
			continue
		}
		key, value, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		switch key {
		case "processor":
			logical++
		case "model name", "Model", "cpu model":
			if model == "" {
				model = value
			}
		case "physical id":
			socket = value
		case "core id":
			coreID = value
		case "cpu cores":
			if n, err := strconv.Atoi(value); err == nil {
				coresPerSocket[socket] = n
			}
		}
	}
	flush()
	cores = len(unique)
	if cores == 0 {
		for _, n := range coresPerSocket {
			cores += n
		}
	}
	if cores == 0 {
		cores = logical
	}
	return model, cores, logical
}

// parseMemTotal reads MemTotal from /proc/meminfo, in bytes.
func parseMemTotal(data string) uint64 {
	for _, line := range strings.Split(data, "\n") {
		if strings.HasPrefix(line, "MemTotal:") {
			fields := strings.Fields(strings.TrimPrefix(line, "MemTotal:"))
			if len(fields) > 0 {
				kb, _ := strconv.ParseUint(fields[0], 10, 64)
				return kb * 1024
			}
		}
	}
	return 0
}

// parseUptime turns /proc/uptime into the boot time.
func parseUptime(data string, now time.Time) *time.Time {
	fields := strings.Fields(data)
	if len(fields) == 0 {
		return nil
	}
	seconds, err := strconv.ParseFloat(fields[0], 64)
	if err != nil {
		return nil
	}
	boot := now.Add(-time.Duration(seconds * float64(time.Second))).UTC().Truncate(time.Second)
	return &boot
}

// realFileSystems are the file systems reported as disks; the rest of
// /proc/mounts is virtual (proc, tmpfs, overlay, squashfs snaps...).
var realFileSystems = map[string]bool{
	"ext2": true, "ext3": true, "ext4": true, "xfs": true, "btrfs": true, "zfs": true, "f2fs": true,
	"jfs": true, "reiserfs": true, "vfat": true, "exfat": true, "ntfs": true, "ntfs3": true, "fuseblk": true,
	"apfs": true, "hfs": true,
}

type mount struct{ device, point, fs string }

// parseMounts lists real file systems in /proc/mounts, once per device.
func parseMounts(data string) []mount {
	var mounts []mount
	seen := map[string]bool{}
	for _, line := range strings.Split(data, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 || !realFileSystems[fields[2]] {
			continue
		}
		point := strings.ReplaceAll(fields[1], `\040`, " ")
		if strings.HasPrefix(point, "/snap/") || strings.HasPrefix(point, "/boot/efi") {
			continue
		}
		if seen[fields[0]] {
			continue
		}
		seen[fields[0]] = true
		mounts = append(mounts, mount{device: fields[0], point: point, fs: fields[2]})
	}
	return mounts
}

// parsePackages reads "name\tversion\tpublisher" lines from dpkg-query, rpm
// or pacman (pacman prints "name version").
func parsePackages(data, source string) []Program {
	var programs []Program
	for _, line := range strings.Split(data, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var fields []string
		if strings.Contains(line, "\t") {
			fields = strings.Split(line, "\t")
		} else {
			fields = strings.Fields(line)
		}
		p := Program{Name: fields[0], Source: source}
		if len(fields) > 1 {
			p.Version = fields[1]
		}
		if len(fields) > 2 && fields[2] != "(none)" {
			p.Publisher = fields[2]
		}
		programs = append(programs, p)
	}
	return programs
}

// macHardware is the part of `system_profiler -json SPHardwareDataType` used.
type macHardware struct {
	SPHardwareDataType []struct {
		MachineName  string `json:"machine_name"`
		MachineModel string `json:"machine_model"`
		ModelNumber  string `json:"model_number"`
		ChipType     string `json:"chip_type"`
		CPUType      string `json:"cpu_type"`
		Serial       string `json:"serial_number"`
		BootROM      string `json:"boot_rom_version"`
	} `json:"SPHardwareDataType"`
	SPApplicationsDataType []struct {
		Name         string `json:"_name"`
		Version      string `json:"version"`
		ObtainedFrom string `json:"obtained_from"`
		Path         string `json:"path"`
		Info         string `json:"info"`
	} `json:"SPApplicationsDataType"`
	SPDisplaysDataType []struct {
		Name  string `json:"_name"`
		Model string `json:"sppci_model"`
	} `json:"SPDisplaysDataType"`
}

// applyMacProfiler fills s from system_profiler's JSON. Apps under /System
// are part of macOS and are left out.
func applyMacProfiler(s *Snapshot, output []byte) error {
	var report macHardware
	if err := json.Unmarshal(output, &report); err != nil {
		return err
	}
	if len(report.SPHardwareDataType) > 0 {
		hw := report.SPHardwareDataType[0]
		s.Manufacturer = "Apple"
		s.Model = strings.TrimSpace(hw.MachineName + " " + hw.ModelNumber)
		if s.Model == "" {
			s.Model = hw.MachineModel
		}
		s.SerialNumber = hw.Serial
		s.BIOSVersion = hw.BootROM
		if s.CPUModel == "" {
			s.CPUModel = hw.ChipType
			if s.CPUModel == "" {
				s.CPUModel = hw.CPUType
			}
		}
	}
	for _, app := range report.SPApplicationsDataType {
		if strings.HasPrefix(app.Path, "/System/") || strings.HasPrefix(app.Path, "/Library/Apple/") {
			continue
		}
		source := "app"
		if app.ObtainedFrom == "mac_app_store" {
			source = "app store"
		}
		s.Software = append(s.Software, Program{Name: app.Name, Version: app.Version, Source: source})
	}
	for _, gpu := range report.SPDisplaysDataType {
		name := gpu.Model
		if name == "" {
			name = gpu.Name
		}
		if name != "" {
			s.GPUs = append(s.GPUs, name)
		}
	}
	return nil
}

// parseBootTime reads `sysctl -n kern.boottime`: "{ sec = 1700000000, usec = 0 } ...".
func parseBootTime(data string) *time.Time {
	_, rest, ok := strings.Cut(data, "sec = ")
	if !ok {
		return nil
	}
	value, _, _ := strings.Cut(rest, ",")
	seconds, err := strconv.ParseInt(strings.TrimSpace(value), 10, 64)
	if err != nil || seconds <= 0 {
		return nil
	}
	boot := time.Unix(seconds, 0).UTC()
	return &boot
}
