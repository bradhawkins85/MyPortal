package live

import (
	"testing"
	"time"
)

func TestParseProcStatCPUs(t *testing.T) {
	cpus, err := parseProcStatCPUs("cpu  100 0 50 800 50 0 0 0 10 0\ncpu0 50 0 25 400 25 0 0 0 0 0\nintr 1\n")
	if err != nil || len(cpus) != 2 {
		t.Fatalf("cpus = %v, %v", cpus, err)
	}
	if cpus[0].total != 1000 || cpus[0].busy != 150 {
		t.Fatalf("aggregate = %+v", cpus[0])
	}
}

func TestParseMeminfoAll(t *testing.T) {
	info, err := parseMeminfoAll("MemTotal: 1000 kB\nMemAvailable: 250 kB\nSwapTotal: 100 kB\nSwapFree: 40 kB\n")
	if err != nil || info.total != 1000*1024 || info.used != 750*1024 || info.swapUsed != 60*1024 {
		t.Fatalf("meminfo = %+v, %v", info, err)
	}
}

func TestParseNetDevTotals(t *testing.T) {
	rx, tx := parseNetDevTotals("Inter-|\n face |bytes\n    lo: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n  eth0: 100 1 0 0 0 0 0 0 200 1 0 0 0 0 0 0\n")
	if rx != 100 || tx != 200 {
		t.Fatalf("rx=%d tx=%d", rx, tx)
	}
}

func TestParseProcPIDStat(t *testing.T) {
	boot := time.Unix(1000, 0)
	line := "42 (my (odd) name) S 7 42 42 0 -1 4194560 100 0 0 0 250 50 0 0 20 0 3 0 500 1000 10 18446744073709551615"
	proc, err := parseProcPIDStat(line, 4096, boot)
	if err != nil {
		t.Fatal(err)
	}
	if proc.Name != "my (odd) name" || proc.PPID != 7 || proc.Threads != 3 || proc.MemoryBytes != 40960 {
		t.Fatalf("proc = %+v", proc)
	}
	if proc.cpuTime != 3*time.Second || proc.Started.Unix() != 1005 {
		t.Fatalf("cpu = %v started = %v", proc.cpuTime, proc.Started)
	}
}

func TestMergeSystemdServices(t *testing.T) {
	units := "ssh.service loaded active running OpenBSD Secure Shell server\n● bad.service not-found inactive dead bad.service\n"
	files := "ssh.service enabled enabled\nidle.service disabled enabled\ngetty@.service enabled enabled\n"
	services := mergeSystemdServices(units, files)
	if len(services) != 3 {
		t.Fatalf("services = %+v", services)
	}
	if services[0].Status != "running" || services[0].StartType != "enabled" || services[0].Description != "OpenBSD Secure Shell server" {
		t.Fatalf("ssh = %+v", services[0])
	}
	if services[1].Status != "missing" || services[2].Name != "idle.service" || services[2].Status != "stopped" {
		t.Fatalf("services = %+v", services)
	}
	if checkUnit("--force") == nil || checkUnit("a b") == nil || checkUnit("ssh.service") != nil {
		t.Fatal("unit name validation")
	}
}
