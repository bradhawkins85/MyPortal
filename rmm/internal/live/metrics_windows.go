package live

import (
	"errors"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	kernel32                 = windows.NewLazySystemDLL("kernel32.dll")
	procGlobalMemoryStatusEx = kernel32.NewProc("GlobalMemoryStatusEx")
)

// systemProcessorPerformance is SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION.
// KernelTime includes IdleTime.
type systemProcessorPerformance struct {
	IdleTime       int64
	KernelTime     int64
	UserTime       int64
	DpcTime        int64
	InterruptTime  int64
	InterruptCount uint32
	_              uint32
}

func readCPUTimes() ([]cpuTimes, error) {
	buf := make([]systemProcessorPerformance, 1024)
	var size uint32
	err := windows.NtQuerySystemInformation(
		windows.SystemProcessorPerformanceInformation,
		unsafe.Pointer(&buf[0]),
		uint32(len(buf))*uint32(unsafe.Sizeof(buf[0])),
		&size,
	)
	if err != nil {
		return nil, err
	}
	count := int(size / uint32(unsafe.Sizeof(buf[0])))
	if count == 0 {
		return nil, errors.New("no processors reported")
	}
	out := make([]cpuTimes, count+1)
	for i, core := range buf[:count] {
		total := uint64(core.KernelTime + core.UserTime)
		idle := uint64(core.IdleTime)
		if idle > total {
			idle = total
		}
		out[i+1] = cpuTimes{busy: total - idle, total: total}
		out[0].busy += total - idle
		out[0].total += total
	}
	return out, nil
}

func directCPUPercent() (float64, bool) { return 0, false }

type memoryStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

func readMemoryInfo() (memoryInfo, error) {
	status := memoryStatusEx{}
	status.Length = uint32(unsafe.Sizeof(status))
	ok, _, err := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&status)))
	if ok == 0 {
		return memoryInfo{}, err
	}
	info := memoryInfo{total: status.TotalPhys}
	if status.AvailPhys <= status.TotalPhys {
		info.used = status.TotalPhys - status.AvailPhys
	}
	// The commit limit is physical memory plus the page files.
	if status.TotalPageFile > status.TotalPhys {
		info.swapTotal = status.TotalPageFile - status.TotalPhys
		committed := status.TotalPageFile - min(status.AvailPageFile, status.TotalPageFile)
		if committed > info.used {
			info.swapUsed = min(committed-info.used, info.swapTotal)
		}
	}
	return info, nil
}

// readNetTotals sums bytes over physical network adapters, so virtual
// switches do not count the same traffic twice.
func readNetTotals() (uint64, uint64, error) {
	var table *windows.MibIfTable2
	if err := windows.GetIfTable2Ex(windows.MibIfEntryNormal, &table); err != nil {
		return 0, 0, err
	}
	defer windows.FreeMibTable(unsafe.Pointer(table))
	rows := unsafe.Slice(&table.Table[0], table.NumEntries)
	var rx, tx uint64
	for _, row := range rows {
		if row.Type == windows.IF_TYPE_SOFTWARE_LOOPBACK || row.InterfaceAndOperStatusFlags&1 == 0 {
			continue
		}
		rx += row.InOctets
		tx += row.OutOctets
	}
	return rx, tx, nil
}

func processCount() int {
	count := 0
	_ = walkProcesses(func(*windows.ProcessEntry32) { count++ })
	return count
}

func uptimeSeconds() uint64 {
	return uint64(windows.DurationSinceBoot().Seconds())
}

func walkProcesses(visit func(*windows.ProcessEntry32)) error {
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return err
	}
	defer windows.CloseHandle(snapshot)
	entry := windows.ProcessEntry32{Size: uint32(unsafe.Sizeof(windows.ProcessEntry32{}))}
	if err := windows.Process32First(snapshot, &entry); err != nil {
		return err
	}
	for {
		visit(&entry)
		if err := windows.Process32Next(snapshot, &entry); err != nil {
			if errors.Is(err, windows.ERROR_NO_MORE_FILES) {
				return nil
			}
			return err
		}
	}
}
