//go:build darwin

package inventory

import (
	"context"
	"strconv"
	"time"
)

func sysctl(ctx context.Context, name string) string {
	out, _ := output(ctx, 10*time.Second, "sysctl", "-n", name)
	return out
}

func collectPlatform(ctx context.Context, s *Snapshot) {
	s.OSName, _ = output(ctx, 10*time.Second, "sw_vers", "-productName")
	s.OSVersion, _ = output(ctx, 10*time.Second, "sw_vers", "-productVersion")
	s.OSBuild, _ = output(ctx, 10*time.Second, "sw_vers", "-buildVersion")
	s.CPUModel = sysctl(ctx, "machdep.cpu.brand_string")
	s.CPUCores, _ = strconv.Atoi(sysctl(ctx, "hw.physicalcpu"))
	s.CPULogical, _ = strconv.Atoi(sysctl(ctx, "hw.logicalcpu"))
	s.RAMBytes, _ = strconv.ParseUint(sysctl(ctx, "hw.memsize"), 10, 64)
	s.LastBoot = parseBootTime(sysctl(ctx, "kern.boottime"))
	if size, free, err := usage("/"); err == nil {
		s.Disks = append(s.Disks, Disk{Name: "/", FileSystem: "apfs", SizeBytes: size, FreeBytes: free, System: true})
	}
	// Listing applications can take a while on a busy Mac.
	out, err := output(ctx, 3*time.Minute, "system_profiler", "-json",
		"SPHardwareDataType", "SPApplicationsDataType", "SPDisplaysDataType")
	if err != nil {
		s.fail("system_profiler", err)
		return
	}
	s.fail("system_profiler", applyMacProfiler(s, []byte(out)))
}
