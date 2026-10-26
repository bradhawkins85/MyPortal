//go:build darwin

package healthcheck

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const platformName = "macos"

var (
	speedLimitRe   = regexp.MustCompile(`CPU_Speed_Limit\s*=\s*(\d+)`)
	memoryFreeRe   = regexp.MustCompile(`free percentage:\s*(\d+)%`)
	bootTimeRe     = regexp.MustCompile(`sec\s*=\s*(\d+)`)
	swapUsedRe     = regexp.MustCompile(`used\s*=\s*([0-9.]+)M`)
	percentValueRe = regexp.MustCompile(`([0-9.]+)\s*%`)
)

// run runs a built-in, read-only macOS tool and returns its output.
func run(ctx context.Context, timeout time.Duration, name string, args ...string) ([]byte, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, name, args...)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return out, fmt.Errorf("%s: %w %s", filepath.Base(name), err, truncate(strings.TrimSpace(stderr.String()), 200))
	}
	return out, nil
}

func sysctl(ctx context.Context, name string) string {
	out, err := run(ctx, 10*time.Second, "/usr/sbin/sysctl", "-n", name)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(out))
}

func platformIdentity() (string, string, error) {
	model := sysctl(context.Background(), "hw.model")
	if model == "" {
		return "", "", fmt.Errorf("hw.model is not available")
	}
	return "Apple", model, nil
}

func asMap(value any) map[string]any {
	m, _ := value.(map[string]any)
	return m
}

func asList(value any) []any {
	l, _ := value.([]any)
	return l
}

func asNumber(value any) (float64, bool) {
	switch v := value.(type) {
	case float64:
		return v, true
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
		return f, err == nil
	}
	return 0, false
}

func round1(value float64) float64 { return float64(int64(value*10+0.5)) / 10 }

func collectPlatformSnapshot(ctx context.Context, eventDays int) (platformSnapshot, error) {
	start := time.Now()
	sections := map[string]any{}
	errs := map[string]string{}

	system := map[string]any{}
	if out, err := run(ctx, 15*time.Second, "/usr/bin/sw_vers"); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			key, value, ok := strings.Cut(line, ":")
			if !ok {
				continue
			}
			switch strings.TrimSpace(key) {
			case "ProductName":
				system["os"] = strings.TrimSpace(value)
			case "ProductVersion":
				system["version"] = strings.TrimSpace(value)
			case "BuildVersion":
				system["build"] = strings.TrimSpace(value)
			}
		}
	} else {
		errs["system"] = err.Error()
	}
	if m := bootTimeRe.FindStringSubmatch(sysctl(ctx, "kern.boottime")); m != nil {
		if sec, err := strconv.ParseInt(m[1], 10, 64); err == nil {
			boot := time.Unix(sec, 0)
			system["last_boot"] = boot.UTC().Format(time.RFC3339)
			system["uptime_hours"] = round1(time.Since(boot).Hours())
		}
	}
	system["model"] = sysctl(ctx, "hw.model")
	sections["system"] = system

	memory := map[string]any{}
	if bytesTotal, err := strconv.ParseFloat(sysctl(ctx, "hw.memsize"), 64); err == nil {
		memory["total_mb"] = int64(bytesTotal / (1 << 20))
	}
	if m := swapUsedRe.FindStringSubmatch(sysctl(ctx, "vm.swapusage")); m != nil {
		if used, err := strconv.ParseFloat(m[1], 64); err == nil {
			memory["swap_used_mb"] = round1(used)
		}
	}
	if out, err := run(ctx, 15*time.Second, "/usr/bin/memory_pressure", "-Q"); err == nil {
		if m := memoryFreeRe.FindStringSubmatch(string(out)); m != nil {
			if free, err := strconv.ParseFloat(m[1], 64); err == nil {
				memory["free_percent"] = free
				memory["used_percent"] = 100 - free
			}
		}
	}
	sections["memory"] = memory

	thermal := map[string]any{}
	if out, err := run(ctx, 15*time.Second, "/usr/bin/pmset", "-g", "therm"); err == nil {
		text := strings.TrimSpace(string(out))
		thermal["pmset"] = truncate(text, 500)
		if m := speedLimitRe.FindStringSubmatch(text); m != nil {
			if limit, err := strconv.Atoi(m[1]); err == nil {
				thermal["cpu_speed_limit"] = limit
			}
		}
	} else {
		errs["thermal"] = err.Error()
	}
	sections["thermal"] = thermal

	out, err := run(ctx, 2*time.Minute, "/usr/sbin/system_profiler", "-json", "-detailLevel", "mini",
		"SPHardwareDataType", "SPStorageDataType", "SPPowerDataType", "SPNVMeDataType", "SPSerialATADataType")
	if err != nil {
		errs["system_profiler"] = err.Error()
	}
	var profile map[string]any
	if len(out) > 0 && json.Unmarshal(out, &profile) == nil {
		addProfile(profile, sections)
	}

	sections["stability"] = map[string]any{"window_days": 30, "panic_count": countPanics(30 * 24 * time.Hour)}

	data, err := json.Marshal(sections)
	if err != nil {
		return platformSnapshot{}, err
	}
	return platformSnapshot{Schema: 1, Sections: data, Errors: errs, ElapsedMS: time.Since(start).Milliseconds()}, nil
}

// addProfile maps system_profiler's JSON to the snapshot's sections.
func addProfile(profile map[string]any, sections map[string]any) {
	system := asMap(sections["system"])
	for _, item := range asList(profile["SPHardwareDataType"]) {
		hw := asMap(item)
		for key, target := range map[string]string{"machine_name": "model_name", "chip_type": "chip", "cpu_type": "chip", "physical_memory": "memory"} {
			if value, ok := hw[key].(string); ok && value != "" {
				system[target] = value
			}
		}
	}
	var volumes []map[string]any
	disks := map[string]map[string]any{}
	for _, item := range asList(profile["SPStorageDataType"]) {
		volume := asMap(item)
		size, _ := asNumber(volume["size_in_bytes"])
		free, _ := asNumber(volume["free_space_in_bytes"])
		entry := map[string]any{
			"mount":       volume["mount_point"],
			"name":        volume["_name"],
			"file_system": volume["file_system"],
			"size_gb":     round1(size / (1 << 30)),
			"free_gb":     round1(free / (1 << 30)),
		}
		if size > 0 {
			entry["free_percent"] = round1(100 * free / size)
		}
		if volume["mount_point"] == "/" {
			entry["boot_volume"] = true
		}
		volumes = append(volumes, entry)
		if drive := asMap(volume["physical_drive"]); drive != nil {
			name, _ := drive["device_name"].(string)
			if name != "" {
				disks[name] = map[string]any{
					"name":         name,
					"media_type":   strings.ToUpper(fmt.Sprint(drive["medium_type"])),
					"bus_type":     drive["protocol"],
					"internal":     drive["is_internal_disk"] == "yes",
					"smart_status": drive["smart_status"],
				}
			}
		}
	}
	for _, key := range []string{"SPNVMeDataType", "SPSerialATADataType"} {
		for _, controller := range asList(profile[key]) {
			for _, item := range asList(asMap(controller)["_items"]) {
				device := asMap(item)
				name, _ := device["device_model"].(string)
				if name == "" {
					name, _ = device["_name"].(string)
				}
				if name == "" {
					continue
				}
				disk := disks[name]
				if disk == nil {
					disk = map[string]any{"name": name}
					disks[name] = disk
				}
				if status, ok := device["smart_status"].(string); ok && status != "" {
					disk["smart_status"] = status
				}
			}
		}
	}
	if volumes != nil {
		sections["volumes"] = volumes
	}
	diskList := make([]map[string]any, 0, len(disks))
	for _, disk := range disks {
		diskList = append(diskList, disk)
	}
	sections["disks"] = diskList
	battery := map[string]any{"present": false}
	for _, item := range asList(profile["SPPowerDataType"]) {
		info := asMap(item)
		health := asMap(info["sppower_battery_health_info"])
		if health == nil {
			continue
		}
		battery["present"] = true
		if cycles, ok := asNumber(health["sppower_battery_cycle_count"]); ok {
			battery["cycle_count"] = int(cycles)
		}
		if condition, ok := health["sppower_battery_health"].(string); ok {
			battery["condition"] = condition
		}
		if capacity, ok := health["sppower_battery_health_maximum_capacity"].(string); ok {
			if m := percentValueRe.FindStringSubmatch(capacity); m != nil {
				if value, err := strconv.ParseFloat(m[1], 64); err == nil {
					battery["health_percent"] = value
				}
			}
		}
		if charge := asMap(info["sppower_battery_charge_info"]); charge != nil {
			if value, ok := asNumber(charge["sppower_battery_state_of_charge"]); ok {
				battery["charge_percent"] = value
			}
		}
	}
	sections["battery"] = battery
}

// countPanics counts kernel panic reports written within the window.
func countPanics(window time.Duration) int {
	count := 0
	cutoff := time.Now().Add(-window)
	for _, pattern := range []string{"/Library/Logs/DiagnosticReports/*.panic", "/Library/Logs/DiagnosticReports/panic-*.ips"} {
		matches, _ := filepath.Glob(pattern)
		for _, match := range matches {
			if info, err := os.Stat(match); err == nil && info.ModTime().After(cutoff) {
				count++
			}
		}
	}
	return count
}

func platformSensors(context.Context, string, bool, machineIdentity) (SensorReport, PawnIOReport) {
	return SensorReport{Status: "unavailable", Detail: "hardware sensors are read on Windows only", Readings: []SensorReading{}},
		PawnIOReport{Status: PawnIOSkippedUnsupported}
}

// platformDiskSMART is not used on macOS: system_profiler reports each
// drive's SMART status in the snapshot.
func platformDiskSMART(context.Context) DiskSMARTReport {
	return DiskSMARTReport{Status: SMARTUnavailable, Source: smartSource, Detail: "CrystalDiskInfo runs on Windows only", Disks: []SMARTDisk{}}
}

func platformCleanupPawnIO(context.Context, string) (bool, error) { return false, nil }
