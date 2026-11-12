"""Names of Windows stop (bug check) codes, and what their four parameters mean.

The names follow Microsoft's *Bug Check Code Reference*. Parameter meanings
are given only for the common codes where the reference states them plainly;
they are passed to the AI troubleshooter with the dump's own values.
"""

from __future__ import annotations

_NONE: tuple[str, ...] = ()

# code -> (name, the meaning of parameters 1-4 or an empty tuple)
STOP_CODES: dict[int, tuple[str, tuple[str, ...]]] = {
    0x01: ("APC_INDEX_MISMATCH", _NONE),
    0x0A: ("IRQL_NOT_LESS_OR_EQUAL", (
        "Memory address referenced", "IRQL at the time of the reference",
        "Access type: bit 0 clear is a read, set is a write; bit 3 set is an execute",
        "Address of the instruction that referenced the memory",
    )),
    0x12: ("TRAP_CAUSE_UNKNOWN", _NONE),
    0x18: ("REFERENCE_BY_POINTER", _NONE),
    0x19: ("BAD_POOL_HEADER", (
        "Kind of pool corruption found", "Pool block header or entry involved",
        "Depends on parameter 1 (often the size or the next entry)", "Depends on parameter 1",
    )),
    0x1A: ("MEMORY_MANAGEMENT", (
        "Subtype of the memory management error", "Depends on the subtype", "Depends on the subtype",
        "Depends on the subtype",
    )),
    0x1E: ("KMODE_EXCEPTION_NOT_HANDLED", (
        "Exception code that was not handled", "Address where the exception occurred",
        "Exception parameter 0", "Exception parameter 1",
    )),
    0x24: ("NTFS_FILE_SYSTEM", (
        "Source file and line number in the NTFS driver",
        "Address of the exception record, when an exception was raised",
        "Address of the context record, when an exception was raised", "Reserved",
    )),
    0x27: ("RDR_FILE_SYSTEM", _NONE),
    0x2E: ("DATA_BUS_ERROR", _NONE),
    0x34: ("CACHE_MANAGER", _NONE),
    0x3B: ("SYSTEM_SERVICE_EXCEPTION", (
        "Exception code that caused the stop", "Address of the instruction that caused it",
        "Address of the context record", "Reserved (zero)",
    )),
    0x3D: ("INTERRUPT_EXCEPTION_NOT_HANDLED", _NONE),
    0x44: ("MULTIPLE_IRP_COMPLETE_REQUESTS", _NONE),
    0x4A: ("IRQL_GT_ZERO_AT_SYSTEM_SERVICE", _NONE),
    0x4E: ("PFN_LIST_CORRUPT", _NONE),
    0x50: ("PAGE_FAULT_IN_NONPAGED_AREA", (
        "Memory address referenced", "Access type: 0 read, 2 write, 10 execute",
        "Address of the instruction that referenced the memory, when known", "Type of page fault",
    )),
    0x51: ("REGISTRY_ERROR", _NONE),
    0x5C: ("HAL_INITIALIZATION_FAILED", _NONE),
    0x67: ("CONFIG_INITIALIZATION_FAILED", _NONE),
    0x6B: ("PROCESS1_INITIALIZATION_FAILED", _NONE),
    0x74: ("BAD_SYSTEM_CONFIG_INFO", _NONE),
    0x76: ("PROCESS_HAS_LOCKED_PAGES", _NONE),
    0x77: ("KERNEL_STACK_INPAGE_ERROR", _NONE),
    0x7A: ("KERNEL_DATA_INPAGE_ERROR", (
        "Lock type or page table entry involved", "Error status of the failed read (an NTSTATUS code)",
        "Current process or the page frame involved", "Virtual address that could not be paged in",
    )),
    0x7B: ("INACCESSIBLE_BOOT_DEVICE", _NONE),
    0x7E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", (
        "Exception code that was not handled", "Address where the exception occurred",
        "Address of the exception record", "Address of the context record",
    )),
    0x7F: ("UNEXPECTED_KERNEL_MODE_TRAP", (
        "Trap number (for example 8 is a double fault)", "Depends on the trap", "Depends on the trap",
        "Depends on the trap",
    )),
    0x80: ("NMI_HARDWARE_FAILURE", _NONE),
    0x8E: ("KERNEL_MODE_EXCEPTION_NOT_HANDLED", _NONE),
    0x92: ("UP_DRIVER_ON_MP_SYSTEM", _NONE),
    0x9C: ("MACHINE_CHECK_EXCEPTION", _NONE),
    0x9F: ("DRIVER_POWER_STATE_FAILURE", (
        "Kind of failure (3: a device object blocked a power request for too long)",
        "Depends on parameter 1 (for 3: the physical device object of the stack)",
        "Depends on parameter 1", "Depends on parameter 1 (for 3: the blocked request)",
    )),
    0xA0: ("INTERNAL_POWER_ERROR", _NONE),
    0xA5: ("ACPI_BIOS_ERROR", _NONE),
    0xBE: ("ATTEMPTED_WRITE_TO_READONLY_MEMORY", (
        "Virtual address of the attempted write", "Page table entry contents", "Reserved", "Reserved",
    )),
    0xC1: ("SPECIAL_POOL_DETECTED_MEMORY_CORRUPTION", _NONE),
    0xC2: ("BAD_POOL_CALLER", (
        "Kind of bad pool request", "Depends on parameter 1", "Depends on parameter 1", "Depends on parameter 1",
    )),
    0xC4: ("DRIVER_VERIFIER_DETECTED_VIOLATION", (
        "Kind of violation Driver Verifier caught", "Depends on parameter 1", "Depends on parameter 1",
        "Depends on parameter 1",
    )),
    0xC5: ("DRIVER_CORRUPTED_EXPOOL", _NONE),
    0xC9: ("DRIVER_VERIFIER_IOMANAGER_VIOLATION", _NONE),
    0xCA: ("PNP_DETECTED_FATAL_ERROR", _NONE),
    0xCE: ("DRIVER_UNLOADED_WITHOUT_CANCELLING_PENDING_OPERATIONS", _NONE),
    0xD1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", (
        "Memory address referenced", "IRQL at the time of the reference",
        "Access type: 0 read, 1 write, 8 execute", "Address of the instruction that referenced the memory",
    )),
    0xD5: ("DRIVER_PAGE_FAULT_IN_FREED_SPECIAL_POOL", _NONE),
    0xD8: ("DRIVER_USED_EXCESSIVE_PTES", _NONE),
    0xDA: ("SYSTEM_PTE_MISUSE", _NONE),
    0xDE: ("POOL_CORRUPTION_IN_FILE_AREA", _NONE),
    0xE2: ("MANUALLY_INITIATED_CRASH", _NONE),
    0xEA: ("THREAD_STUCK_IN_DEVICE_DRIVER", _NONE),
    0xED: ("UNMOUNTABLE_BOOT_VOLUME", _NONE),
    0xEF: ("CRITICAL_PROCESS_DIED", (
        "Process object of the critical process", "0 when a process terminated, 1 when a thread did",
        "Reserved", "Reserved",
    )),
    0xF4: ("CRITICAL_OBJECT_TERMINATION", (
        "Kind of object that ended: 3 is a process, 6 is a thread", "The object that ended",
        "Image file name of the process", "Pointer to an explanatory message",
    )),
    0xF7: ("DRIVER_OVERRAN_STACK_BUFFER", _NONE),
    0xFC: ("ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY", (
        "Virtual address the code tried to execute", "Page table entry contents", "Reserved", "Reserved",
    )),
    0xFE: ("BUGCODE_USB_DRIVER", _NONE),
    0x101: ("CLOCK_WATCHDOG_TIMEOUT", (
        "Clock interrupt timeout, in clock ticks", "Zero", "Processor control block of the unresponsive processor",
        "Index of the unresponsive processor",
    )),
    0x109: ("CRITICAL_STRUCTURE_CORRUPTION", _NONE),
    0x10D: ("WDF_VIOLATION", _NONE),
    0x10E: ("VIDEO_MEMORY_MANAGEMENT_INTERNAL", _NONE),
    0x113: ("VIDEO_DXGKRNL_FATAL_ERROR", _NONE),
    0x116: ("VIDEO_TDR_FAILURE", (
        "Internal graphics recovery context", "Address inside the display driver that was responsible",
        "Error code of the last failed operation", "Internal context-dependent data",
    )),
    0x117: ("VIDEO_TDR_TIMEOUT_DETECTED", _NONE),
    0x119: ("VIDEO_SCHEDULER_INTERNAL_ERROR", _NONE),
    0x11B: ("DRIVER_RETURNED_HOLDING_CANCEL_LOCK", _NONE),
    0x11D: ("EVENT_TRACING_FATAL_ERROR", _NONE),
    0x122: ("WHEA_INTERNAL_ERROR", _NONE),
    0x124: ("WHEA_UNCORRECTABLE_ERROR", (
        "Kind of hardware error source (0: machine check exception)", "Address of the hardware error record",
        "High 32 bits of the machine check status, when parameter 1 is 0",
        "Low 32 bits of the machine check status, when parameter 1 is 0",
    )),
    0x127: ("PAGE_NOT_ZERO", _NONE),
    0x12B: ("FAULTY_HARDWARE_CORRUPTED_PAGE", _NONE),
    0x133: ("DPC_WATCHDOG_VIOLATION", (
        "0: one deferred procedure call ran too long; 1: the system spent too long at a raised IRQL",
        "Time count or watchdog period", "Time allotment or internal data", "Internal data",
    )),
    0x135: ("REGISTRY_FILTER_DRIVER_EXCEPTION", _NONE),
    0x139: ("KERNEL_SECURITY_CHECK_FAILURE", (
        "Kind of corruption found (2: stack buffer overrun, 3: corrupt list entry)",
        "Address of the trap frame", "Address of the exception record", "Reserved",
    )),
    0x13A: ("KERNEL_MODE_HEAP_CORRUPTION", _NONE),
    0x13C: ("INVALID_IO_BOOST_STATE", _NONE),
    0x144: ("BUGCODE_USB3_DRIVER", _NONE),
    0x14F: ("PDC_WATCHDOG_TIMEOUT", _NONE),
    0x154: ("UNEXPECTED_STORE_EXCEPTION", _NONE),
    0x15F: ("CONNECTED_STANDBY_WATCHDOG_TIMEOUT_LIVEDUMP", _NONE),
    0x162: ("KERNEL_AUTO_BOOST_INVALID_LOCK_RELEASE", _NONE),
    0x17E: ("MICROCODE_REVISION_MISMATCH", _NONE),
    0x18B: ("SECURE_KERNEL_ERROR", _NONE),
    0x192: ("KERNEL_AUTO_BOOST_LOCK_ACQUISITION_WITH_RAISED_IRQL", _NONE),
    0x1A0: ("TTM_WATCHDOG_TIMEOUT", _NONE),
    0x1C8: ("MANUALLY_INITIATED_POWER_BUTTON_HOLD", _NONE),
    0x1CA: ("SYNTHETIC_WATCHDOG_TIMEOUT", _NONE),
    0x1D3: ("WIN32K_CALLOUT_WATCHDOG_BUGCHECK", _NONE),
    0xC000021A: ("WINLOGON_FATAL_ERROR", _NONE),
    0xC0000221: ("STATUS_IMAGE_CHECKSUM_MISMATCH", _NONE),
    0x1000007E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED_M", _NONE),
    0x1000008E: ("KERNEL_MODE_EXCEPTION_NOT_HANDLED_M", _NONE),
}


def _code(code: int | None) -> int | None:
    if isinstance(code, bool) or not isinstance(code, int) or code < 0 or code > 0xFFFFFFFF:
        return None
    return code


def name(code: int | None) -> str:
    """The stop code's name, or ``""`` when it is not in the table."""

    value = _code(code)
    return STOP_CODES[value][0] if value in STOP_CODES else ""


def label(code: int | None) -> str:
    """``0x000000D1 DRIVER_IRQL_NOT_LESS_OR_EQUAL``; the bare hex value for an unknown code."""

    value = _code(code)
    if value is None:
        return "Unknown"
    known = name(value)
    return "0x" + format(value, "08X") + (" " + known if known else "")


def parameter_meanings(code: int | None) -> list[str]:
    """What the code's four parameters mean, when documented plainly."""

    value = _code(code)
    return list(STOP_CODES[value][1]) if value in STOP_CODES else []


__all__ = ["STOP_CODES", "label", "name", "parameter_meanings"]
