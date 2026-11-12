// Package minidump decodes Windows kernel minidumps (the small blue screen
// dumps in %SystemRoot%\Minidump) into a summary MyPortal can analyse. The
// dump file itself never leaves the computer.
//
// The layout below was checked against these public definitions:
//
//   - DUMP_HEADER64: kdmp-parser (kdmp-parser-structs.h, with static asserts)
//     and Rekall (plugins/overlays/windows/crashdump.py).
//   - DUMP_HEADER32: Rekall.
//   - TRIAGE_DUMP64, DUMP_UNLOADED_DRIVERS64, DUMP_STRING: the Singularity RDK
//     base/Windows/Inc/Dump.h and dumplib's DMPTemplate.bt.
//   - The driver list entry (a name offset followed by a 64-bit loader
//     entry): dumplib's DMPTemplate.bt.
//
// Two points the sources leave open are handled defensively: DUMP_STRING's
// length is documented as bytes by one source and characters by the other, so
// a name also ends at its first NUL; and the driver checksum offset has a
// single source, so it is informational only.
package minidump

const (
	// Both headers.
	offSignature    = 0x000 // "PAGE"
	offValidDump    = 0x004 // "DU64" (64-bit) or "DUMP" (32-bit)
	offMajorVersion = 0x008
	offMinorVersion = 0x00C // the Windows build number

	// DUMP_HEADER64.
	off64MachineImageType   = 0x030
	off64NumberProcessors   = 0x034
	off64BugCheckCode       = 0x038
	off64BugCheckParameters = 0x040 // four uint64
	off64ContextRecord      = 0x348
	off64Exception          = 0xF00 // EXCEPTION_RECORD64
	off64DumpType           = 0xF98
	off64SystemTime         = 0xFA8 // FILETIME
	off64SystemUpTime       = 0x1030
	headerSize64            = 0x2000 // the triage block follows

	exceptionAddressOffset = 0x10
	contextRipAMD64        = 0xF8
	contextPcARM64         = 0x108

	// DUMP_HEADER32.
	off32MachineImageType   = 0x020
	off32NumberProcessors   = 0x024
	off32BugCheckCode       = 0x028
	off32BugCheckParameters = 0x02C // four uint32
	headerMin32             = 0x03C

	// TRIAGE_DUMP64, relative to headerSize64. Its offsets are file offsets.
	triageUnloadedDriversOffset = 0x18
	triageCallStackOffset       = 0x28
	triageSizeOfCallStack       = 0x2C
	triageDriverListOffset      = 0x30
	triageDriverCount           = 0x34
	triageStringPoolOffset      = 0x38
	triageStringPoolSize        = 0x3C
	triageMinSize               = 0x40

	// Driver list entry.
	driverEntrySize     = 0x90
	driverNameOffset    = 0x00 // file offset of a DUMP_STRING
	driverDllBase       = 0x38
	driverSizeOfImage   = 0x48
	driverCheckSum      = 0x80
	driverTimeDateStamp = 0x88

	// Unloaded driver list: a count, padding, then fixed-size entries.
	unloadedHeaderSize  = 0x08
	unloadedEntrySize   = 0x38
	unloadedNameOffset  = 0x10
	unloadedNameBytes   = 24 // 12 UTF-16 characters
	unloadedStartOffset = 0x28
	unloadedEndOffset   = 0x30

	machineAMD64 = 0x8664
	machineARM64 = 0xAA64
	machineI386  = 0x014C

	// 100 ns intervals between 1601-01-01 and 1970-01-01.
	filetimeUnixEpoch = 116444736000000000

	// Limits.
	maxReadBytes       = 8 << 20
	maxDrivers         = 512
	maxUnloadedDrivers = 64
	maxStackBytes      = 256 << 10
	maxStackDrivers    = 64
	maxNameLength      = 64
	maxNameChars       = 260
)
