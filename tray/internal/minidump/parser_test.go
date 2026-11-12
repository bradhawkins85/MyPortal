package minidump

import (
	"os"
	"reflect"
	"testing"
	"time"
)

func TestParseHeaderAndDrivers(t *testing.T) {
	s := Parse(baseDump().bytes())
	if !s.Supported || s.Error != "" {
		t.Fatalf("supported=%v error=%q", s.Supported, s.Error)
	}
	if s.Architecture != "x64" || s.BugcheckCode != 0xD1 || s.WindowsBuild != 22631 || s.ProcessorCount != 8 {
		t.Fatalf("%+v", s)
	}
	if len(s.BugcheckParameters) != 4 || s.BugcheckParameters[3] != "0xfffff80512345678" ||
		s.BugcheckParameters[0] != "0x0000000000000028" {
		t.Fatal(s.BugcheckParameters)
	}
	if s.CrashedAt != "2026-11-11T02:01:00Z" || s.UptimeSeconds != 86400 || s.DumpType != 4 {
		t.Fatal(s.CrashedAt, s.UptimeSeconds, s.DumpType)
	}
	if len(s.Drivers) != 2 || s.Drivers[1].Name != "rtwlane.sys" || s.Drivers[1].Timestamp != "2021-03-04T05:06:07Z" ||
		s.Drivers[1].Base != "0xfffff80512340000" || s.Drivers[1].Size != 0x900000 || s.Drivers[1].Checksum != 123456 {
		t.Fatalf("%+v", s.Drivers)
	}
	if len(s.UnloadedDrivers) != 1 || s.UnloadedDrivers[0].Name != "dump_storahc" ||
		s.UnloadedDrivers[0].Start != "0xfffff80600000000" || s.UnloadedDrivers[0].End != "0xfffff80600020000" {
		t.Fatalf("%+v", s.UnloadedDrivers)
	}
}

func TestParseResolvesAddressesAndSuspect(t *testing.T) {
	s := Parse(baseDump().bytes())
	if s.FaultingAddress == nil || s.FaultingAddress.Driver != "rtwlane.sys" || s.FaultingAddress.Offset != "0x5678" ||
		s.FaultingAddress.Address != "0xfffff80512345678" {
		t.Fatalf("%+v", s.FaultingAddress)
	}
	if len(s.ParameterDrivers) != 1 || s.ParameterDrivers[0].Parameter != 4 || s.ParameterDrivers[0].Driver != "rtwlane.sys" {
		t.Fatalf("%+v", s.ParameterDrivers)
	}
	want := []StackRef{{"rtwlane.sys", "0x5678"}, {"ntoskrnl.exe", "0x3f5a10"}} // consecutive duplicate collapsed
	if !reflect.DeepEqual(s.StackDrivers, want) {
		t.Fatalf("%+v", s.StackDrivers)
	}
	if s.SuspectDriver != "rtwlane.sys" {
		t.Fatal(s.SuspectDriver)
	}
}

func TestSuspectEmptyWhenOnlyCoreImages(t *testing.T) {
	d := baseDump()
	d.exception = testNtBase + 0x100
	d.params = [4]uint64{0, 0, 0, testNtBase + 0x200}
	d.stack = []uint64{testNtBase + 0x300, 5}
	s := Parse(d.bytes())
	if s.SuspectDriver != "" || !s.Supported {
		t.Fatalf("suspect=%q supported=%v", s.SuspectDriver, s.Supported)
	}
}

func TestSuspectFallsBackToStack(t *testing.T) {
	d := baseDump()
	d.exception = 0
	d.params = [4]uint64{1, 2, 3, 4}
	d.stack = []uint64{testNtBase + 0x300, testRtwBase + 0x10}
	s := Parse(d.bytes())
	if s.FaultingAddress != nil || len(s.ParameterDrivers) != 0 {
		t.Fatalf("%+v %+v", s.FaultingAddress, s.ParameterDrivers)
	}
	if s.SuspectDriver != "rtwlane.sys" {
		t.Fatal(s.SuspectDriver)
	}
}

func TestExceptionAddressZeroUsesContextIP(t *testing.T) {
	d := baseDump()
	d.exception = 0
	d.contextIP = testRtwBase + 0x42
	s := Parse(d.bytes())
	if s.FaultingAddress == nil || s.FaultingAddress.Driver != "rtwlane.sys" || s.FaultingAddress.Offset != "0x42" {
		t.Fatalf("%+v", s.FaultingAddress)
	}
}

func TestArm64UsesPc(t *testing.T) {
	d := baseDump()
	d.machine = machineARM64
	d.exception = 0
	d.contextIP = testRtwBase + 0x99
	s := Parse(d.bytes())
	if s.Architecture != "arm64" || s.FaultingAddress == nil || s.FaultingAddress.Driver != "rtwlane.sys" {
		t.Fatalf("%s %+v", s.Architecture, s.FaultingAddress)
	}
}

func Test32BitHeader(t *testing.T) {
	d := baseDump()
	d.valid = "DUMP"
	d.machine = 0x14c
	d.bugcheck = 0x7E
	s := Parse(d.bytes())
	if s.Supported || s.Architecture != "x86" || s.BugcheckCode != 0x7E || s.Error != "32-bit dumps are not supported" {
		t.Fatalf("%+v", s)
	}
	if len(s.BugcheckParameters) != 4 {
		t.Fatal(s.BugcheckParameters)
	}
}

func TestNotADump(t *testing.T) {
	for _, data := range [][]byte{nil, {}, []byte("MDMP\x93\xa7\x00\x00 not a kernel dump at all"), []byte("PAGE")} {
		s := Parse(data)
		if s.Supported || s.Error != "not a kernel dump" {
			t.Fatalf("%q -> %+v", data, s)
		}
	}
}

func TestTruncatedDumpKeepsHeader(t *testing.T) {
	s := Parse(baseDump().bytes()[:headerSize64])
	if s.Supported || s.BugcheckCode != 0xD1 || s.Error == "" {
		t.Fatalf("%+v", s)
	}
}

func TestHostileOffsetsDoNotPanic(t *testing.T) {
	cases := map[string]func(*testDump){
		"driver list past end":  func(d *testDump) { d.driverList = u32p(0xFFFFFF00) },
		"driver count huge":     func(d *testDump) { d.driverCount = u32p(0xFFFFFFFF) },
		"stack offset past end": func(d *testDump) { d.stackOffset = u32p(0xFFFFFFF0) },
		"stack size huge":       func(d *testDump) { d.stackSize = u32p(0xFFFFFFFF) },
		"name offset past end":  func(d *testDump) { d.nameOffset = u32p(0xFFFFFFF0) },
		"name length huge":      func(d *testDump) { d.nameLength = u32p(0xFFFFFFFF) },
		"unloaded count huge":   func(d *testDump) { d.unloadCount = u64p(1 << 40) },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			d := baseDump()
			mutate(&d)
			s := Parse(d.bytes())
			if len(s.Drivers) > maxDrivers || len(s.UnloadedDrivers) > maxUnloadedDrivers || len(s.StackDrivers) > maxStackDrivers {
				t.Fatalf("limits exceeded: %d %d %d", len(s.Drivers), len(s.UnloadedDrivers), len(s.StackDrivers))
			}
			if s.BugcheckCode != 0xD1 {
				t.Fatalf("header lost: %+v", s)
			}
		})
	}
}

func TestStackDriversAreCapped(t *testing.T) {
	d := baseDump()
	d.stack = nil
	for i := 0; i < 200; i++ { // alternate so nothing collapses
		d.stack = append(d.stack, testRtwBase+uint64(i), testNtBase+uint64(i))
	}
	if s := Parse(d.bytes()); len(s.StackDrivers) != maxStackDrivers {
		t.Fatal(len(s.StackDrivers))
	}
}

func TestDriverNameIsSanitised(t *testing.T) {
	d := baseDump()
	d.drivers[1].name = `C:\Windows\System32\drivers\<script>évil.sys`
	s := Parse(d.bytes())
	if s.Drivers[1].Name != "scriptvil.sys" {
		t.Fatalf("%q", s.Drivers[1].Name)
	}
	long := ""
	for i := 0; i < 100; i++ {
		long += "a"
	}
	d.drivers[1].name = long + ".sys"
	if s := Parse(d.bytes()); len(s.Drivers[1].Name) != maxNameLength {
		t.Fatalf("%d", len(s.Drivers[1].Name))
	}
}

func TestZeroTimesAreEmpty(t *testing.T) {
	d := baseDump()
	d.systemTime = time.Time{}
	d.drivers[1].timestamp = 0
	s := Parse(d.bytes())
	if s.CrashedAt != "" || s.Drivers[1].Timestamp != "" {
		t.Fatalf("%q %q", s.CrashedAt, s.Drivers[1].Timestamp)
	}
}

func TestIsCoreImage(t *testing.T) {
	if !IsCoreImage("NTOSKRNL.EXE") || !IsCoreImage("hal.dll") || IsCoreImage("rtwlane.sys") || IsCoreImage("") {
		t.Fatal("core image list")
	}
}

func FuzzParse(f *testing.F) {
	f.Add(baseDump().bytes())
	d := baseDump()
	d.valid = "DUMP"
	f.Add(d.bytes())
	f.Fuzz(func(t *testing.T, data []byte) {
		s := Parse(data)
		if len(s.Drivers) > maxDrivers || len(s.StackDrivers) > maxStackDrivers || len(s.UnloadedDrivers) > maxUnloadedDrivers {
			t.Fatal("limits exceeded")
		}
	})
}

// TestRealDump checks the parser against a real blue screen dump when
// MYPORTAL_TEST_MINIDUMP names one; synthetic dumps cannot prove the layout.
func TestRealDump(t *testing.T) {
	path := os.Getenv("MYPORTAL_TEST_MINIDUMP")
	if path == "" {
		t.Skip("MYPORTAL_TEST_MINIDUMP is not set")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(data) > maxReadBytes {
		data = data[:maxReadBytes]
	}
	s := Parse(data)
	if !s.Supported || s.BugcheckCode == 0 || s.CrashedAt == "" {
		t.Fatalf("%+v", s)
	}
	core := false
	for _, d := range s.Drivers {
		core = core || IsCoreImage(d.Name)
	}
	if len(s.Drivers) < 20 || !core {
		t.Fatalf("drivers=%d core=%v", len(s.Drivers), core)
	}
	t.Logf("stop 0x%X suspect %q drivers %d stack %v", s.BugcheckCode, s.SuspectDriver, len(s.Drivers), s.StackDrivers)
}
