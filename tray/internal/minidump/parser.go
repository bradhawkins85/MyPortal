package minidump

import (
	"encoding/binary"
	"fmt"
	"strings"
	"time"
)

// Summary is what the tray app reports to MyPortal for one dump. 64-bit
// values are hex strings because JSON numbers cannot hold them.
type Summary struct {
	FileName       string `json:"file_name"`
	FileSize       int64  `json:"file_size"`
	FileModifiedAt string `json:"file_modified_at"`

	Supported bool   `json:"supported"`
	Error     string `json:"error"`

	Architecture       string   `json:"architecture"`
	BugcheckCode       uint32   `json:"bugcheck_code"`
	BugcheckParameters []string `json:"bugcheck_parameters"`
	CrashedAt          string   `json:"crashed_at"`
	UptimeSeconds      uint64   `json:"uptime_seconds"`
	WindowsBuild       uint32   `json:"windows_build"`
	ProcessorCount     uint32   `json:"processor_count"`
	DumpType           uint32   `json:"dump_type"`

	FaultingAddress  *AddressRef      `json:"faulting_address"`
	ParameterDrivers []ParameterRef   `json:"parameter_drivers"`
	StackDrivers     []StackRef       `json:"stack_drivers"`
	SuspectDriver    string           `json:"suspect_driver"`
	Drivers          []Driver         `json:"drivers"`
	UnloadedDrivers  []UnloadedDriver `json:"unloaded_drivers"`
}

// AddressRef is an address inside a loaded driver.
type AddressRef struct {
	Address string `json:"address"`
	Driver  string `json:"driver"`
	Offset  string `json:"offset"`
}

// ParameterRef is a bug check parameter (1-4) that points into a driver.
type ParameterRef struct {
	Parameter int    `json:"parameter"`
	Address   string `json:"address"`
	Driver    string `json:"driver"`
	Offset    string `json:"offset"`
}

// StackRef is a value on the crash stack that points into a driver.
type StackRef struct {
	Driver string `json:"driver"`
	Offset string `json:"offset"`
}

// Driver is a driver that was loaded when the computer crashed.
type Driver struct {
	Name      string `json:"name"`
	Base      string `json:"base"`
	Size      uint32 `json:"size"`
	Timestamp string `json:"timestamp"`
	Checksum  uint32 `json:"checksum"`

	base uint64
}

// UnloadedDriver is a driver unloaded shortly before the crash.
type UnloadedDriver struct {
	Name  string `json:"name"`
	Start string `json:"start"`
	End   string `json:"end"`
}

var coreImages = map[string]bool{
	"ntoskrnl.exe": true, "ntkrnlmp.exe": true, "ntkrnlpa.exe": true, "ntkrpamp.exe": true,
	"hal.dll": true, "halmacpi.dll": true, "kdcom.dll": true, "ci.dll": true, "clfs.sys": true,
	"win32k.sys": true, "win32kbase.sys": true, "win32kfull.sys": true, "fltmgr.sys": true,
	"ndis.sys": true, "tcpip.sys": true, "wdf01000.sys": true, "acpi.sys": true, "ntfs.sys": true,
	"volsnap.sys": true, "storport.sys": true, "dxgkrnl.sys": true, "dxgmms1.sys": true, "dxgmms2.sys": true,
}

// IsCoreImage reports whether name is a Windows core image: one that is on
// nearly every crash stack and so is rarely the cause itself.
func IsCoreImage(name string) bool {
	return coreImages[strings.ToLower(name)]
}

func u32(data []byte, off uint64) (uint32, bool) {
	if off > uint64(len(data)) || uint64(len(data))-off < 4 {
		return 0, false
	}
	return binary.LittleEndian.Uint32(data[off:]), true
}

func u64(data []byte, off uint64) (uint64, bool) {
	if off > uint64(len(data)) || uint64(len(data))-off < 8 {
		return 0, false
	}
	return binary.LittleEndian.Uint64(data[off:]), true
}

func hexAddress(v uint64) string { return fmt.Sprintf("0x%016x", v) }
func hexOffset(v uint64) string  { return fmt.Sprintf("0x%x", v) }

func architecture(machine uint32) string {
	switch machine {
	case machineAMD64:
		return "x64"
	case machineARM64:
		return "arm64"
	case machineI386:
		return "x86"
	}
	return "unknown"
}

// cleanName reduces a driver name to its base file name and to the
// characters a file name needs, so nothing from the dump can carry markup.
func cleanName(name string) string {
	if i := strings.LastIndexAny(name, `\/`); i >= 0 {
		name = name[i+1:]
	}
	var b strings.Builder
	for _, r := range name {
		if b.Len() >= maxNameLength {
			break
		}
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9', r == '.', r == '_', r == '-':
			b.WriteRune(r)
		}
	}
	return b.String()
}

// utf16String reads up to maxChars UTF-16LE characters at off, stopping at
// the first NUL or the end of the data.
func utf16String(data []byte, off uint64, maxChars uint64) string {
	if maxChars > maxNameChars {
		maxChars = maxNameChars
	}
	var b strings.Builder
	for i := uint64(0); i < maxChars; i++ {
		pos := off + 2*i
		if pos > uint64(len(data)) || uint64(len(data))-pos < 2 {
			break
		}
		unit := binary.LittleEndian.Uint16(data[pos:])
		if unit == 0 {
			break
		}
		b.WriteRune(rune(unit))
	}
	return b.String()
}

func filetimeString(ft uint64) string {
	if ft <= filetimeUnixEpoch {
		return ""
	}
	seconds := (ft - filetimeUnixEpoch) / 10_000_000
	if seconds > 1<<35 { // far beyond any real date
		return ""
	}
	return time.Unix(int64(seconds), 0).UTC().Format(time.RFC3339)
}

func emptySummary() Summary {
	return Summary{
		BugcheckParameters: []string{},
		ParameterDrivers:   []ParameterRef{},
		StackDrivers:       []StackRef{},
		Drivers:            []Driver{},
		UnloadedDrivers:    []UnloadedDriver{},
		Architecture:       "unknown",
	}
}

// Parse decodes a kernel minidump. It never panics and never fails outright:
// a dump it cannot decode comes back with Supported false and the reason in
// Error, keeping whatever header fields were readable.
func Parse(data []byte) Summary {
	s := emptySummary()
	if len(data) > maxReadBytes {
		data = data[:maxReadBytes]
	}
	if len(data) < 8 || string(data[offSignature:offSignature+4]) != "PAGE" {
		s.Error = "not a kernel dump"
		return s
	}
	switch string(data[offValidDump : offValidDump+4]) {
	case "DU64":
		parse64(data, &s)
	case "DUMP":
		parse32(data, &s)
	default:
		s.Error = "not a kernel dump"
	}
	return s
}

func parse32(data []byte, s *Summary) {
	s.Error = "32-bit dumps are not supported"
	if len(data) < headerMin32 {
		return
	}
	s.WindowsBuild, _ = u32(data, offMinorVersion)
	machine, _ := u32(data, off32MachineImageType)
	s.Architecture = architecture(machine)
	if s.Architecture == "unknown" {
		s.Architecture = "x86"
	}
	s.ProcessorCount, _ = u32(data, off32NumberProcessors)
	s.BugcheckCode, _ = u32(data, off32BugCheckCode)
	for i := uint64(0); i < 4; i++ {
		p, _ := u32(data, off32BugCheckParameters+4*i)
		s.BugcheckParameters = append(s.BugcheckParameters, hexAddress(uint64(p)))
	}
}

func parse64(data []byte, s *Summary) {
	if len(data) < headerSize64 {
		s.Error = "the dump is truncated"
		if len(data) < off64BugCheckParameters+32 {
			return
		}
	}
	s.WindowsBuild, _ = u32(data, offMinorVersion)
	machine, _ := u32(data, off64MachineImageType)
	s.Architecture = architecture(machine)
	s.ProcessorCount, _ = u32(data, off64NumberProcessors)
	s.BugcheckCode, _ = u32(data, off64BugCheckCode)
	var params [4]uint64
	for i := uint64(0); i < 4; i++ {
		params[i], _ = u64(data, off64BugCheckParameters+8*i)
		s.BugcheckParameters = append(s.BugcheckParameters, hexAddress(params[i]))
	}
	if s.Error != "" {
		return
	}
	s.DumpType, _ = u32(data, off64DumpType)
	systemTime, _ := u64(data, off64SystemTime)
	s.CrashedAt = filetimeString(systemTime)
	uptime, _ := u64(data, off64SystemUpTime)
	s.UptimeSeconds = uptime / 10_000_000
	if s.UptimeSeconds > 10*365*24*3600 {
		s.UptimeSeconds = 0
	}
	if machine != machineAMD64 && machine != machineARM64 {
		s.Error = "unsupported processor architecture"
		return
	}

	triage := uint64(headerSize64)
	if uint64(len(data)) < triage+triageMinSize {
		s.Error = "the dump is truncated"
		return
	}
	s.Supported = true
	fail := func(reason string) {
		if s.Error == "" {
			s.Error = reason
		}
	}

	readDrivers(data, triage, s, fail)
	readUnloadedDrivers(data, triage, s, fail)

	faulting, _ := u64(data, off64Exception+exceptionAddressOffset)
	if faulting == 0 {
		ip := uint64(contextRipAMD64)
		if machine == machineARM64 {
			ip = contextPcARM64
		}
		faulting, _ = u64(data, off64ContextRecord+ip)
	}
	if d, offset, ok := resolve(s.Drivers, faulting); ok {
		s.FaultingAddress = &AddressRef{Address: hexAddress(faulting), Driver: d.Name, Offset: hexOffset(offset)}
	}
	for i, p := range params {
		if d, offset, ok := resolve(s.Drivers, p); ok {
			s.ParameterDrivers = append(s.ParameterDrivers, ParameterRef{
				Parameter: i + 1, Address: hexAddress(p), Driver: d.Name, Offset: hexOffset(offset),
			})
		}
	}
	readStack(data, triage, s, fail)
	s.SuspectDriver = suspect(s)
	if len(s.Drivers) == 0 {
		s.Supported = false
		fail("the dump has no driver list")
	}
}

func readDrivers(data []byte, triage uint64, s *Summary, fail func(string)) {
	listOffset, _ := u32(data, triage+triageDriverListOffset)
	count, _ := u32(data, triage+triageDriverCount)
	if count > maxDrivers {
		count = maxDrivers
	}
	for i := uint64(0); i < uint64(count); i++ {
		entry := uint64(listOffset) + i*driverEntrySize
		if entry > uint64(len(data)) || uint64(len(data))-entry < driverEntrySize {
			fail("the driver list is incomplete")
			return
		}
		nameOffset, _ := u32(data, entry+driverNameOffset)
		base, _ := u64(data, entry+driverDllBase)
		size, _ := u32(data, entry+driverSizeOfImage)
		checksum, _ := u32(data, entry+driverCheckSum)
		stamp, _ := u32(data, entry+driverTimeDateStamp)
		name := ""
		if length, ok := u32(data, uint64(nameOffset)); ok {
			name = cleanName(utf16String(data, uint64(nameOffset)+4, uint64(length)))
		}
		if name == "" || base == 0 || size == 0 {
			continue
		}
		timestamp := ""
		if stamp != 0 {
			timestamp = time.Unix(int64(stamp), 0).UTC().Format(time.RFC3339)
		}
		s.Drivers = append(s.Drivers, Driver{
			Name: name, Base: hexAddress(base), Size: size, Timestamp: timestamp, Checksum: checksum, base: base,
		})
	}
}

func readUnloadedDrivers(data []byte, triage uint64, s *Summary, fail func(string)) {
	listOffset, _ := u32(data, triage+triageUnloadedDriversOffset)
	if listOffset == 0 {
		return
	}
	count, ok := u32(data, uint64(listOffset))
	if !ok {
		fail("the unloaded driver list is incomplete")
		return
	}
	if count > maxUnloadedDrivers {
		count = maxUnloadedDrivers
	}
	for i := uint64(0); i < uint64(count); i++ {
		entry := uint64(listOffset) + unloadedHeaderSize + i*unloadedEntrySize
		if entry > uint64(len(data)) || uint64(len(data))-entry < unloadedEntrySize {
			return
		}
		name := cleanName(utf16String(data, entry+unloadedNameOffset, unloadedNameBytes/2))
		start, _ := u64(data, entry+unloadedStartOffset)
		end, _ := u64(data, entry+unloadedEndOffset)
		if name == "" {
			continue
		}
		s.UnloadedDrivers = append(s.UnloadedDrivers, UnloadedDriver{
			Name: name, Start: hexAddress(start), End: hexAddress(end),
		})
	}
}

func readStack(data []byte, triage uint64, s *Summary, fail func(string)) {
	stackOffset, _ := u32(data, triage+triageCallStackOffset)
	stackSize, _ := u32(data, triage+triageSizeOfCallStack)
	if stackSize > maxStackBytes {
		stackSize = maxStackBytes
	}
	if stackOffset == 0 || stackSize == 0 {
		return
	}
	if uint64(stackOffset) > uint64(len(data)) {
		fail("the call stack is missing")
		return
	}
	end := uint64(stackOffset) + uint64(stackSize)
	if end > uint64(len(data)) {
		end = uint64(len(data))
	}
	for pos := uint64(stackOffset); pos+8 <= end && len(s.StackDrivers) < maxStackDrivers; pos += 8 {
		value := binary.LittleEndian.Uint64(data[pos:])
		d, offset, ok := resolve(s.Drivers, value)
		if !ok {
			continue
		}
		ref := StackRef{Driver: d.Name, Offset: hexOffset(offset)}
		if n := len(s.StackDrivers); n > 0 && s.StackDrivers[n-1] == ref {
			continue
		}
		s.StackDrivers = append(s.StackDrivers, ref)
	}
}

func resolve(drivers []Driver, address uint64) (Driver, uint64, bool) {
	if address == 0 {
		return Driver{}, 0, false
	}
	for _, d := range drivers {
		if address >= d.base && address-d.base < uint64(d.Size) {
			return d, address - d.base, true
		}
	}
	return Driver{}, 0, false
}

// suspect picks the first driver that is not a Windows core image among the
// faulting address, the bug check parameters and the stack, in that order.
func suspect(s *Summary) string {
	if s.FaultingAddress != nil && !IsCoreImage(s.FaultingAddress.Driver) {
		return s.FaultingAddress.Driver
	}
	for _, p := range s.ParameterDrivers {
		if !IsCoreImage(p.Driver) {
			return p.Driver
		}
	}
	for _, ref := range s.StackDrivers {
		if !IsCoreImage(ref.Driver) {
			return ref.Driver
		}
	}
	return ""
}
