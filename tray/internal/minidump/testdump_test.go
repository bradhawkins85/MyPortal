package minidump

import (
	"encoding/binary"
	"time"
	"unicode/utf16"
)

// testDump builds a synthetic 64-bit kernel minidump with the layout in
// layout.go, so the parser can be tested without a real crash dump.
type testDump struct {
	valid       string // "DU64" or "DUMP"
	machine     uint32
	processors  uint32
	build       uint32
	bugcheck    uint32
	params      [4]uint64
	systemTime  time.Time // zero: FILETIME 0
	uptime      time.Duration
	dumpType    uint32
	exception   uint64
	contextIP   uint64
	drivers     []testDriver
	unloaded    []testUnloaded
	stack       []uint64
	rawNames    map[int]string // driver index -> name written without the default
	driverCount *uint32        // overrides
	driverList  *uint32
	stackOffset *uint32
	stackSize   *uint32
	nameOffset  *uint32 // overrides driver 0's name offset
	nameLength  *uint32 // overrides driver 0's DUMP_STRING length
	unloadCount *uint64
}

type testDriver struct {
	name      string
	base      uint64
	size      uint32
	timestamp uint32
	checksum  uint32
}

type testUnloaded struct {
	name       string
	start, end uint64
}

const (
	testNtBase  = 0xfffff80500000000
	testRtwBase = 0xfffff80512340000
)

func baseDump() testDump {
	return testDump{
		valid:      "DU64",
		machine:    machineAMD64,
		processors: 8,
		build:      22631,
		bugcheck:   0xD1,
		params:     [4]uint64{0x28, 2, 0, testRtwBase + 0x5678},
		systemTime: time.Date(2026, 11, 11, 2, 1, 0, 0, time.UTC),
		uptime:     24 * time.Hour,
		dumpType:   4,
		exception:  testRtwBase + 0x5678,
		drivers: []testDriver{
			{name: "ntoskrnl.exe", base: testNtBase, size: 0x1000000, timestamp: 1700000000, checksum: 1},
			{name: "rtwlane.sys", base: testRtwBase, size: 0x900000,
				timestamp: uint32(time.Date(2021, 3, 4, 5, 6, 7, 0, time.UTC).Unix()), checksum: 123456},
		},
		unloaded: []testUnloaded{{name: "dump_storahci.sys", start: 0xfffff80600000000, end: 0xfffff80600020000}},
		stack:    []uint64{0x1, testRtwBase + 0x5678, testRtwBase + 0x5678, testNtBase + 0x3f5a10},
	}
}

func filetime(t time.Time) uint64 {
	if t.IsZero() {
		return 0
	}
	return uint64(t.Unix())*10_000_000 + filetimeUnixEpoch
}

func utf16le(s string) []byte {
	units := utf16.Encode([]rune(s))
	out := make([]byte, len(units)*2)
	for i, u := range units {
		binary.LittleEndian.PutUint16(out[i*2:], u)
	}
	return out
}

func (d testDump) bytes() []byte {
	le := binary.LittleEndian
	if d.valid == "DUMP" {
		buf := make([]byte, 0x1000)
		copy(buf, "PAGEDUMP")
		le.PutUint32(buf[offMinorVersion:], d.build)
		le.PutUint32(buf[off32MachineImageType:], d.machine)
		le.PutUint32(buf[off32NumberProcessors:], d.processors)
		le.PutUint32(buf[off32BugCheckCode:], d.bugcheck)
		for i, p := range d.params {
			le.PutUint32(buf[off32BugCheckParameters+4*i:], uint32(p))
		}
		return buf
	}

	const (
		unloadedOff = 0x2100
		stackOff    = 0x3000
		driversOff  = 0x4000
		stringsOff  = 0x6000
		total       = 0x8000
	)
	buf := make([]byte, total)
	copy(buf, "PAGE")
	copy(buf[4:], d.valid)
	le.PutUint32(buf[offMajorVersion:], 0xF)
	le.PutUint32(buf[offMinorVersion:], d.build)
	le.PutUint32(buf[off64MachineImageType:], d.machine)
	le.PutUint32(buf[off64NumberProcessors:], d.processors)
	le.PutUint32(buf[off64BugCheckCode:], d.bugcheck)
	for i, p := range d.params {
		le.PutUint64(buf[off64BugCheckParameters+8*i:], p)
	}
	ipOffset := contextRipAMD64
	if d.machine == machineARM64 {
		ipOffset = contextPcARM64
	}
	le.PutUint64(buf[off64ContextRecord+ipOffset:], d.contextIP)
	le.PutUint64(buf[off64Exception+exceptionAddressOffset:], d.exception)
	le.PutUint32(buf[off64DumpType:], d.dumpType)
	le.PutUint64(buf[off64SystemTime:], filetime(d.systemTime))
	le.PutUint64(buf[off64SystemUpTime:], uint64(d.uptime/100))

	// Unloaded drivers.
	count := uint64(len(d.unloaded))
	if d.unloadCount != nil {
		count = *d.unloadCount
	}
	le.PutUint64(buf[unloadedOff:], count)
	for i, u := range d.unloaded {
		entry := buf[unloadedOff+8+i*unloadedEntrySize:]
		name := utf16le(u.name)
		if len(name) > unloadedNameBytes {
			name = name[:unloadedNameBytes]
		}
		copy(entry[unloadedNameOffset:], name)
		le.PutUint64(entry[unloadedStartOffset:], u.start)
		le.PutUint64(entry[unloadedEndOffset:], u.end)
	}

	// Call stack.
	for i, v := range d.stack {
		le.PutUint64(buf[stackOff+8*i:], v)
	}

	// Drivers and their names.
	stringAt := stringsOff
	for i, drv := range d.drivers {
		entry := buf[driversOff+i*driverEntrySize:]
		nameOffset := uint32(stringAt)
		if i == 0 && d.nameOffset != nil {
			nameOffset = *d.nameOffset
		}
		le.PutUint32(entry[driverNameOffset:], nameOffset)
		le.PutUint64(entry[driverDllBase:], drv.base)
		le.PutUint32(entry[driverSizeOfImage:], drv.size)
		le.PutUint32(entry[driverCheckSum:], drv.checksum)
		le.PutUint32(entry[driverTimeDateStamp:], drv.timestamp)
		name := utf16le(drv.name)
		length := uint32(len(name) / 2)
		if i == 0 && d.nameLength != nil {
			length = *d.nameLength
		}
		le.PutUint32(buf[stringAt:], length)
		copy(buf[stringAt+4:], name)
		stringAt += 4 + len(name) + 2 // NUL terminator
		stringAt = (stringAt + 7) &^ 7
	}

	triage := buf[headerSize64:]
	le.PutUint32(triage[triageUnloadedDriversOffset:], unloadedOff)
	put := func(off int, def uint32, override *uint32) {
		if override != nil {
			def = *override
		}
		le.PutUint32(triage[off:], def)
	}
	put(triageCallStackOffset, stackOff, d.stackOffset)
	put(triageSizeOfCallStack, uint32(8*len(d.stack)), d.stackSize)
	put(triageDriverListOffset, driversOff, d.driverList)
	put(triageDriverCount, uint32(len(d.drivers)), d.driverCount)
	le.PutUint32(triage[triageStringPoolOffset:], stringsOff)
	le.PutUint32(triage[triageStringPoolSize:], uint32(stringAt-stringsOff))
	return buf
}

func u32p(v uint32) *uint32 { return &v }
func u64p(v uint64) *uint64 { return &v }
