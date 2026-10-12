package inventory

import (
	"strconv"
	"strings"
	"testing"
	"time"
)

const windowsOutput = `{"manufacturer":"Dell Inc.","model":"Latitude 7440","domain":"corp.example.com","workgroup":null,
"ram_bytes":34089181184,"serial":"ABC1234","bios_version":"1.12.0","os_name":"Microsoft Windows 11 Pro",
"os_version":"10.0.22631","display_version":"23H2","build":"22631","ubr":4317,
"last_boot":"2026-10-11T22:15:03.5000000Z","system_drive":"C:",
"cpus":[{"name":"13th Gen Intel(R) Core(TM) i7-1365U","cores":10,"logical":12}],
"memory":[{"slot":"DIMM A","size":17179869184,"speed":5200,"manufacturer":"Samsung","part":"M425R2GA3BB0 "},null],
"volumes":[{"name":"D:","label":"Data","fs":"NTFS","size":"500000000000","free":100},
           {"name":"C:","label":"","fs":"NTFS","size":511000000000,"free":200000000000}],
"drives":[{"model":"NVMe Samsung SSD 980","size":512110190592,"media":"SSD","bus":"NVMe","serial":"S1"},
          {"model":"Virtual","size":10,"media":"Unspecified","bus":"SAS","serial":null}],
"gpus":["Intel(R) Iris(R) Xe Graphics",null],
"network":[{"name":"Intel(R) Wi-Fi 6E AX211","mac":"AA-BB-CC-DD-EE-FF","addresses":["10.0.0.5","fe80::1"]}],
"software":[{"name":"Google Chrome","version":"129.0.6668.90","publisher":"Google LLC","install_date":"20241001","scope":"machine"},
            {"name":"Zoom Workplace","version":"6.2","publisher":"Zoom","install_date":"","scope":"user"},
            {"name":"Google Chrome","version":"129.0.6668.90","publisher":"Google LLC","install_date":"","scope":"machine"},
            {"name":"  ","version":"1","publisher":null,"install_date":null,"scope":"machine"}],
"errors":["gpus: Access denied"]}`

func TestApplyWindows(t *testing.T) {
	s := Snapshot{OSName: "Windows"}
	if err := applyWindows(&s, []byte(windowsOutput)); err != nil {
		t.Fatal(err)
	}
	s = Normalise(s)
	if s.Manufacturer != "Dell Inc." || s.Model != "Latitude 7440" || s.SerialNumber != "ABC1234" || s.Domain != "corp.example.com" {
		t.Fatalf("system: %+v", s)
	}
	if s.OSName != "Microsoft Windows 11 Pro" || s.OSVersion != "23H2" || s.OSBuild != "22631.4317" {
		t.Fatalf("os: %q %q %q", s.OSName, s.OSVersion, s.OSBuild)
	}
	if s.RAMBytes != 34089181184 || s.CPUCores != 10 || s.CPULogical != 12 || !strings.Contains(s.CPUModel, "i7-1365U") {
		t.Fatalf("cpu/ram: %+v", s)
	}
	if s.LastBoot == nil || !s.LastBoot.Equal(time.Date(2026, 10, 11, 22, 15, 3, 500000000, time.UTC)) {
		t.Fatalf("last boot: %v", s.LastBoot)
	}
	if len(s.Disks) != 2 || s.Disks[0].Name != "C:" || !s.Disks[0].System || s.Disks[1].System || s.Disks[1].SizeBytes != 500000000000 {
		t.Fatalf("disks: %+v", s.Disks)
	}
	if len(s.PhysicalDisks) != 2 || s.PhysicalDisks[1].MediaType != "" || s.PhysicalDisks[0].BusType != "NVMe" {
		t.Fatalf("physical disks: %+v", s.PhysicalDisks)
	}
	if len(s.Memory) != 1 || s.Memory[0].PartNumber != "M425R2GA3BB0" || s.Memory[0].SpeedMHz != 5200 {
		t.Fatalf("memory: %+v", s.Memory)
	}
	if len(s.GPUs) != 1 || len(s.Network) != 1 || s.Network[0].MAC != "aa:bb:cc:dd:ee:ff" || len(s.Network[0].Addresses) != 1 {
		t.Fatalf("gpus/network: %+v %+v", s.GPUs, s.Network)
	}
	if len(s.Software) != 2 || s.Software[0].Name != "Google Chrome" || s.Software[0].InstallDate != "2024-10-01" || s.Software[1].Source != "user" {
		t.Fatalf("software: %+v", s.Software)
	}
	if len(s.Errors) != 1 {
		t.Fatalf("errors: %v", s.Errors)
	}
}

func TestApplyWindowsRejectsGarbage(t *testing.T) {
	s := Snapshot{}
	if err := applyWindows(&s, []byte("Get-CimInstance : Access denied")); err == nil {
		t.Fatal("expected an error")
	}
}

const cpuinfo = `processor	: 0
model name	: AMD EPYC 7763
physical id	: 0
core id		: 0
cpu cores	: 2

processor	: 1
model name	: AMD EPYC 7763
physical id	: 0
core id		: 0
cpu cores	: 2

processor	: 2
model name	: AMD EPYC 7763
physical id	: 0
core id		: 1
cpu cores	: 2

processor	: 3
model name	: AMD EPYC 7763
physical id	: 0
core id		: 1
cpu cores	: 2
`

func TestLinuxParsers(t *testing.T) {
	model, cores, logical := parseCPUInfo(cpuinfo)
	if model != "AMD EPYC 7763" || cores != 2 || logical != 4 {
		t.Fatalf("cpu: %q %d %d", model, cores, logical)
	}
	// ARM boards list processors without core ids.
	if _, cores, logical := parseCPUInfo("processor : 0\n\nprocessor : 1\n\nModel : Raspberry Pi 4\n"); cores != 2 || logical != 2 {
		t.Fatalf("arm: %d %d", cores, logical)
	}
	if ram := parseMemTotal("MemTotal:       16303428 kB\nMemFree: 1 kB\n"); ram != 16303428*1024 {
		t.Fatalf("ram: %d", ram)
	}
	release := parseOSRelease("NAME=\"Ubuntu\"\nVERSION_ID=\"24.04\"\nPRETTY_NAME=\"Ubuntu 24.04.1 LTS\"\n# comment\n")
	if release["PRETTY_NAME"] != "Ubuntu 24.04.1 LTS" || release["VERSION_ID"] != "24.04" {
		t.Fatalf("os-release: %v", release)
	}
	now := time.Date(2026, 10, 12, 0, 0, 0, 0, time.UTC)
	if boot := parseUptime("3600.25 100.00", now); boot == nil || !boot.Equal(now.Add(-time.Hour-time.Second)) {
		t.Fatalf("uptime: %v", boot)
	}
	mounts := parseMounts(`/dev/sda2 / ext4 rw 0 0
proc /proc proc rw 0 0
/dev/sda2 /var/lib/docker/btrfs ext4 rw 0 0
/dev/sdb1 /mnt/My\040Data xfs rw 0 0
/dev/loop0 /snap/core/1 squashfs ro 0 0
/dev/sda1 /boot/efi vfat rw 0 0
tmpfs /run tmpfs rw 0 0
`)
	if len(mounts) != 2 || mounts[0].point != "/" || mounts[1].point != "/mnt/My Data" {
		t.Fatalf("mounts: %+v", mounts)
	}
	programs := parsePackages("openssl\t3.0.13-0ubuntu3\tUbuntu Developers\ncurl\t8.5.0-2\t(none)\n\n", "dpkg")
	if len(programs) != 2 || programs[0].Version != "3.0.13-0ubuntu3" || programs[1].Publisher != "" {
		t.Fatalf("dpkg: %+v", programs)
	}
	if programs := parsePackages("linux 6.10.1.arch1-1\n", "pacman"); programs[0].Name != "linux" || programs[0].Version != "6.10.1.arch1-1" {
		t.Fatalf("pacman: %+v", programs)
	}
}

func TestMacParsers(t *testing.T) {
	s := Snapshot{}
	out := `{"SPHardwareDataType":[{"machine_name":"MacBook Pro","model_number":"MRX33LL/A","chip_type":"Apple M3 Pro",
"serial_number":"C02XYZ","boot_rom_version":"10151.1.1"}],
"SPApplicationsDataType":[{"_name":"Safari","version":"18.0","path":"/System/Applications/Safari.app"},
{"_name":"Slack","version":"4.40","obtained_from":"mac_app_store","path":"/Applications/Slack.app"},
{"_name":"Firefox","version":"131.0","obtained_from":"identified_developer","path":"/Applications/Firefox.app"}],
"SPDisplaysDataType":[{"_name":"Apple M3 Pro","sppci_model":"Apple M3 Pro"}]}`
	if err := applyMacProfiler(&s, []byte(out)); err != nil {
		t.Fatal(err)
	}
	if s.Manufacturer != "Apple" || s.Model != "MacBook Pro MRX33LL/A" || s.SerialNumber != "C02XYZ" || s.CPUModel != "Apple M3 Pro" {
		t.Fatalf("hardware: %+v", s)
	}
	if len(s.Software) != 2 || s.Software[0].Source != "app store" || len(s.GPUs) != 1 {
		t.Fatalf("apps: %+v", s.Software)
	}
	if boot := parseBootTime("{ sec = 1728000000, usec = 12 } Fri Oct  4 00:00:00 2024"); boot == nil || boot.Unix() != 1728000000 {
		t.Fatalf("boot: %v", boot)
	}
}

func TestNormaliseCleansAndCaps(t *testing.T) {
	s := Snapshot{Manufacturer: "To Be Filled By O.E.M.", Model: " OptiPlex 7090 ", SerialNumber: strings.Repeat("x", 400)}
	for i := 0; i < MaxSoftware+10; i++ {
		s.Software = append(s.Software, Program{Name: "pkg" + strings.Repeat("a", i%7) + string(rune('a'+i%26)) + strconv.Itoa(i)})
	}
	s = Normalise(s)
	if s.Manufacturer != "" || s.Model != "OptiPlex 7090" || len(s.SerialNumber) != maxText {
		t.Fatalf("fields: %q %q %d", s.Manufacturer, s.Model, len(s.SerialNumber))
	}
	if len(s.Software) != MaxSoftware || len(s.Errors) != 1 || s.Disks == nil || s.GPUs == nil {
		t.Fatalf("caps: %d %v", len(s.Software), s.Errors)
	}
}
