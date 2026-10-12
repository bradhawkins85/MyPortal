//go:build linux

package inventory

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func readText(path string) string {
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(data))
}

func collectPlatform(ctx context.Context, s *Snapshot) {
	release := parseOSRelease(readText("/etc/os-release"))
	s.OSName = release["PRETTY_NAME"]
	if s.OSName == "" {
		s.OSName = release["NAME"]
	}
	s.OSVersion = release["VERSION_ID"]
	s.OSBuild = readText("/proc/sys/kernel/osrelease")
	s.Manufacturer = readText("/sys/class/dmi/id/sys_vendor")
	s.Model = readText("/sys/class/dmi/id/product_name")
	s.SerialNumber = readText("/sys/class/dmi/id/product_serial")
	s.BIOSVersion = readText("/sys/class/dmi/id/bios_version")
	if s.Model == "" {
		// Raspberry Pi and other boards without DMI.
		s.Model = readText("/proc/device-tree/model")
	}
	s.CPUModel, s.CPUCores, s.CPULogical = parseCPUInfo(readText("/proc/cpuinfo"))
	s.RAMBytes = parseMemTotal(readText("/proc/meminfo"))
	s.LastBoot = parseUptime(readText("/proc/uptime"), time.Now())
	if domain := readText("/proc/sys/kernel/domainname"); domain != "(none)" {
		s.Domain = domain
	}

	mounts, err := os.ReadFile("/proc/mounts")
	s.fail("disks", err)
	for _, m := range parseMounts(string(mounts)) {
		size, free, err := usage(m.point)
		if err != nil || size == 0 {
			continue
		}
		s.Disks = append(s.Disks, Disk{Name: m.point, Label: m.device, FileSystem: m.fs, SizeBytes: size,
			FreeBytes: free, System: m.point == "/"})
	}

	s.PhysicalDisks = blockDevices()
	s.Software = linuxPackages(ctx, s)
}

// blockDevices lists the drives in /sys/block, leaving out loop, RAM,
// device-mapper and RAID devices.
func blockDevices() []PhysicalDisk {
	entries, err := os.ReadDir("/sys/block")
	if err != nil {
		return nil
	}
	var disks []PhysicalDisk
	for _, entry := range entries {
		name := entry.Name()
		if strings.HasPrefix(name, "loop") || strings.HasPrefix(name, "ram") || strings.HasPrefix(name, "zram") ||
			strings.HasPrefix(name, "dm-") || strings.HasPrefix(name, "md") || strings.HasPrefix(name, "sr") {
			continue
		}
		base := filepath.Join("/sys/block", name)
		sectors, _ := strconv.ParseUint(readText(filepath.Join(base, "size")), 10, 64)
		if sectors == 0 {
			continue
		}
		disk := PhysicalDisk{Model: readText(filepath.Join(base, "device", "model")), SizeBytes: sectors * 512,
			Serial: readText(filepath.Join(base, "device", "serial"))}
		if disk.Model == "" {
			disk.Model = name
		}
		switch readText(filepath.Join(base, "queue", "rotational")) {
		case "0":
			disk.MediaType = "SSD"
		case "1":
			disk.MediaType = "HDD"
		}
		if strings.HasPrefix(name, "nvme") {
			disk.BusType = "NVMe"
		}
		disks = append(disks, disk)
	}
	return disks
}

// linuxPackages asks whichever package manager the distribution uses.
func linuxPackages(ctx context.Context, s *Snapshot) []Program {
	managers := []struct {
		name, source string
		args         []string
	}{
		{"dpkg-query", "dpkg", []string{"-W", "-f", "${Package}\t${Version}\t${Maintainer}\n"}},
		{"rpm", "rpm", []string{"-qa", "--queryformat", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{VENDOR}\n"}},
		{"pacman", "pacman", []string{"-Q"}},
	}
	for _, manager := range managers {
		if _, err := exec.LookPath(manager.name); err != nil {
			continue
		}
		out, err := output(ctx, 2*time.Minute, manager.name, manager.args...)
		if err != nil {
			s.fail("software", err)
			return nil
		}
		return parsePackages(out, manager.source)
	}
	return nil
}
