package checks

import (
	"context"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func TestParsePing(t *testing.T) {
	windows := `Pinging 1.1.1.1 with 32 bytes of data:
Reply from 1.1.1.1: bytes=32 time=12ms TTL=57
Reply from 1.1.1.1: bytes=32 time<1ms TTL=57
Request timed out.
Reply from 1.1.1.1: bytes=32 Zeit=14ms TTL=57
`
	received, average := ParsePing(windows)
	if received != 3 || average == nil || *average != 9 {
		t.Fatalf("windows: got %d %v", received, average)
	}
	linux := `PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=12.5 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=57 time=13.5 ms
`
	received, average = ParsePing(linux)
	if received != 2 || average == nil || *average != 13 {
		t.Fatalf("linux: got %d %v", received, average)
	}
	received, average = ParsePing("Reply from 10.0.0.1: Destination host unreachable.\n")
	if received != 0 || average != nil {
		t.Fatalf("unreachable: got %d %v", received, average)
	}
}

func TestPingRejectsOptions(t *testing.T) {
	result := Ping(context.Background(), "-f")
	if result.Received != 0 || result.Error == "" {
		t.Fatalf("got %+v", result)
	}
	if args := pingArgs("windows", "gw"); args[len(args)-1] != "gw" || args[0] != "-n" {
		t.Fatalf("got %v", args)
	}
}

func TestCPUFromTimes(t *testing.T) {
	before, err := parseProcStat("cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 1 2 3 4\n")
	if err != nil {
		t.Fatal(err)
	}
	after, _ := parseProcStat("cpu  150 0 150 800 100 0 0 0 0 0\n")
	percent, ok := busyPercent(before, after)
	if !ok || percent != 50 {
		t.Fatalf("got %v %v", percent, ok)
	}
	if _, ok := busyPercent(after, before); ok {
		t.Fatal("counters going backwards must not give a value")
	}
	readings := []cpuTimes{before, after}
	sampler := &timesSampler{read: func() (cpuTimes, error) {
		next := readings[0]
		readings = readings[1:]
		return next, nil
	}}
	if _, ok := sampler.Percent(); ok {
		t.Fatal("the first reading has nothing to compare with")
	}
	if percent, ok := sampler.Percent(); !ok || percent != 50 {
		t.Fatalf("got %v %v", percent, ok)
	}
}

func TestParseMeminfoAndMounts(t *testing.T) {
	total, available, err := parseMeminfo("MemTotal:       16000000 kB\nMemFree: 100 kB\nMemAvailable:    4000000 kB\n")
	if err != nil || total != 16000000*1024 || available != 4000000*1024 {
		t.Fatalf("got %d %d %v", total, available, err)
	}
	mounts := parseMounts(`/dev/sda1 / ext4 rw 0 0
proc /proc proc rw 0 0
/dev/sda1 /var/lib/docker ext4 rw 0 0
/dev/sdb1 /mnt/My\040Data xfs rw 0 0
tmpfs /run tmpfs rw 0 0
`)
	if len(mounts) != 2 || mounts[0] != "/" || mounts[1] != "/mnt/My Data" {
		t.Fatalf("got %v", mounts)
	}
}

func TestNetworkRates(t *testing.T) {
	counters := parseNetDev(`Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
  eth0: 6000 50 0 0 0 0 0 0 3000 20 0 0 0 0 0 0
`)
	if len(counters) != 1 || counters["eth0"].rx != 6000 || counters["eth0"].tx != 3000 {
		t.Fatalf("got %v", counters)
	}
	before := map[string]netCounters{"eth0": {rx: 0, tx: 0}, "wlan0": {rx: 500, tx: 500}}
	after := map[string]netCounters{"eth0": {rx: 6000, tx: 3000}, "wlan0": {rx: 100, tx: 900}}
	rates := Rates(before, after, 2*time.Second)
	if len(rates) != 1 || rates[0] != (client.InterfaceRate{Name: "eth0", RxBps: 3000, TxBps: 1500}) {
		t.Fatalf("got %v", rates)
	}
}

func TestMacParsers(t *testing.T) {
	available, err := parseVMStat(`Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10.
Pages active:                            500.
Pages inactive:                           20.
Pages speculative:                         5.
Pages purgeable:                           5.
`)
	if err != nil || available != 40*16384 {
		t.Fatalf("got %d %v", available, err)
	}
	counters := parseNetstat(`Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                          100     0      20000      100     0      20000     0
en0        1500  <Link#6>    aa:bb:cc:dd:ee:ff     500     0      90000      400     0      30000     0
en0        1500  192.168.1     192.168.1.5         500     -      90000      400     -      30000     -
utun0      1380  <Link#7>                            0     0          0        2     0        200     0
`)
	if counters["en0"] != (netCounters{rx: 90000, tx: 30000}) || counters["utun0"] != (netCounters{rx: 0, tx: 200}) {
		t.Fatalf("got %v", counters)
	}
	if _, ok := counters["lo0"]; ok {
		t.Fatal("loopback must be left out")
	}
	percent, ok := parseTopCPU("Processes: 1\nCPU usage: 10.0% user, 5.0% sys, 85.0% idle\nCPU usage: 20.0% user, 5.5% sys, 74.5% idle\n")
	if !ok || percent != 25.5 {
		t.Fatalf("got %v %v", percent, ok)
	}
}

func TestCheckServiceRejectsOptions(t *testing.T) {
	status := CheckService(context.Background(), client.CheckService{Name: "--now", Expected: "running", Fix: "start"})
	if status.Error == "" || status.Action != "" {
		t.Fatalf("got %+v", status)
	}
}
