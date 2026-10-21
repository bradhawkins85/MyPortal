//go:build windows

package checks

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

var (
	kernel32                 = windows.NewLazySystemDLL("kernel32.dll")
	procGetSystemTimes       = kernel32.NewProc("GetSystemTimes")
	procGlobalMemoryStatusEx = kernel32.NewProc("GlobalMemoryStatusEx")
)

func filetime(ft windows.Filetime) uint64 {
	return uint64(ft.HighDateTime)<<32 | uint64(ft.LowDateTime)
}

func newCPUSampler() cpuSampler {
	return &timesSampler{read: func() (cpuTimes, error) {
		var idle, kernel, user windows.Filetime
		r, _, err := procGetSystemTimes.Call(
			uintptr(unsafe.Pointer(&idle)), uintptr(unsafe.Pointer(&kernel)), uintptr(unsafe.Pointer(&user)))
		if r == 0 {
			return cpuTimes{}, err
		}
		// Kernel time includes idle time.
		return cpuTimes{idle: filetime(idle), total: filetime(kernel) + filetime(user)}, nil
	}}
}

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

func readMemory() (uint64, uint64, error) {
	status := memoryStatusEx{}
	status.Length = uint32(unsafe.Sizeof(status))
	r, _, err := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&status)))
	if r == 0 {
		return 0, 0, err
	}
	return status.TotalPhys, status.AvailPhys, nil
}

// readDisks reports every fixed drive.
func readDisks() ([]client.DiskUsage, error) {
	buffer := make([]uint16, 512)
	n, err := windows.GetLogicalDriveStrings(uint32(len(buffer)), &buffer[0])
	if err != nil {
		return nil, err
	}
	var disks []client.DiskUsage
	for _, root := range splitNulls(buffer[:n]) {
		path, err := windows.UTF16PtrFromString(root)
		if err != nil || windows.GetDriveType(path) != windows.DRIVE_FIXED {
			continue
		}
		var available, total, free uint64
		if err := windows.GetDiskFreeSpaceEx(path, &available, &total, &free); err != nil || total == 0 {
			continue
		}
		disks = append(disks, client.DiskUsage{Name: strings.TrimSuffix(root, `\`), TotalBytes: total, FreeBytes: free})
	}
	return disks, nil
}

// splitNulls splits GetLogicalDriveStrings' list of NUL-terminated strings.
func splitNulls(buffer []uint16) []string {
	var roots []string
	start := 0
	for i, c := range buffer {
		if c == 0 {
			if i > start {
				roots = append(roots, windows.UTF16ToString(buffer[start:i]))
			}
			start = i + 1
		}
	}
	return roots
}

// readNetwork reads the byte counters of physical interfaces that are up.
func readNetwork() (map[string]netCounters, error) {
	var table *windows.MibIfTable2
	if err := windows.GetIfTable2Ex(windows.MibIfTableNormal, &table); err != nil {
		return nil, err
	}
	defer windows.FreeMibTable(unsafe.Pointer(table))
	rows := unsafe.Slice(&table.Table[0], table.NumEntries)
	counters := map[string]netCounters{}
	for i := range rows {
		row := &rows[i]
		hardware := row.InterfaceAndOperStatusFlags&0x1 != 0
		filter := row.InterfaceAndOperStatusFlags&0x2 != 0
		if !hardware || filter || row.OperStatus != 1 /* IfOperStatusUp */ {
			continue
		}
		name := windows.UTF16ToString(row.Alias[:])
		if name == "" {
			name = fmt.Sprintf("Interface %d", row.InterfaceIndex)
		}
		counters[name] = netCounters{rx: row.InOctets, tx: row.OutOctets}
	}
	return counters, nil
}

var serviceStates = map[svc.State]string{
	svc.Stopped:         "stopped",
	svc.StartPending:    "start_pending",
	svc.StopPending:     "stop_pending",
	svc.Running:         "running",
	svc.ContinuePending: "continue_pending",
	svc.PausePending:    "pause_pending",
	svc.Paused:          "paused",
}

// openService opens the service with only the rights the caller needs.
// mgr.Connect and Mgr.OpenService ask for full access, which Windows refuses
// for services with locked-down permissions (Defender, Sense and other
// protected services) even when the agent runs as SYSTEM.
func openService(name string, access uint32) (*mgr.Mgr, *mgr.Service, error) {
	h, err := windows.OpenSCManager(nil, nil, windows.SC_MANAGER_CONNECT)
	if err != nil {
		return nil, nil, err
	}
	m := &mgr.Mgr{Handle: h}
	namePtr, err := windows.UTF16PtrFromString(name)
	if err != nil {
		m.Disconnect()
		return nil, nil, err
	}
	sh, err := windows.OpenService(h, namePtr, access)
	if err != nil {
		m.Disconnect()
		return nil, nil, err
	}
	return m, &mgr.Service{Name: name, Handle: sh}, nil
}

func stateName(state svc.State) string {
	if name, ok := serviceStates[state]; ok {
		return name
	}
	return "unknown"
}

func serviceStatus(_ context.Context, name string) (string, error) {
	m, s, err := openService(name, windows.SERVICE_QUERY_STATUS)
	if errors.Is(err, windows.ERROR_SERVICE_DOES_NOT_EXIST) {
		return "missing", nil
	}
	if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
		// Some services refuse even a status query; the service list
		// still reports their state.
		return enumeratedStatus(name, err)
	}
	if err != nil {
		return "unknown", err
	}
	defer m.Disconnect()
	defer s.Close()
	status, err := s.Query()
	if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
		return enumeratedStatus(name, err)
	}
	if err != nil {
		return "unknown", err
	}
	return stateName(status.State), nil
}

// enumeratedStatus finds the service's state in the service control
// manager's list of services, which needs no access to the service itself.
// cause is returned if the service is not in the list either.
func enumeratedStatus(name string, cause error) (string, error) {
	h, err := windows.OpenSCManager(nil, nil, windows.SC_MANAGER_ENUMERATE_SERVICE)
	if err != nil {
		return "unknown", cause
	}
	defer windows.CloseServiceHandle(h)
	var needed, count, resume uint32
	buf := make([]byte, 64*1024)
	for {
		err := windows.EnumServicesStatusEx(h, windows.SC_ENUM_PROCESS_INFO, windows.SERVICE_WIN32,
			windows.SERVICE_STATE_ALL, &buf[0], uint32(len(buf)), &needed, &count, &resume, nil)
		if err != nil && !errors.Is(err, windows.ERROR_MORE_DATA) {
			return "unknown", cause
		}
		if count > 0 {
			entries := unsafe.Slice((*windows.ENUM_SERVICE_STATUS_PROCESS)(unsafe.Pointer(&buf[0])), count)
			for _, entry := range entries {
				if strings.EqualFold(windows.UTF16PtrToString(entry.ServiceName), name) {
					return stateName(svc.State(entry.ServiceStatusProcess.CurrentState)), nil
				}
			}
		}
		if err == nil || (count == 0 && int(needed) <= len(buf)) {
			return "unknown", cause
		}
		if int(needed) > len(buf) {
			buf = make([]byte, needed)
		}
	}
}

// waitFor polls the service until it reaches state or a minute passes.
func waitFor(ctx context.Context, s *mgr.Service, state svc.State) error {
	deadline := time.Now().Add(time.Minute)
	for time.Now().Before(deadline) {
		status, err := s.Query()
		if err != nil {
			return err
		}
		if status.State == state {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
	return fmt.Errorf("the service did not become %s within a minute", serviceStates[state])
}

func startService(ctx context.Context, name string) error {
	m, s, err := openService(name, windows.SERVICE_START|windows.SERVICE_QUERY_STATUS)
	if err != nil {
		return err
	}
	defer m.Disconnect()
	defer s.Close()
	if err := s.Start(); err != nil && !errors.Is(err, windows.ERROR_SERVICE_ALREADY_RUNNING) {
		return err
	}
	return waitFor(ctx, s, svc.Running)
}

func stopService(ctx context.Context, name string) error {
	m, s, err := openService(name, windows.SERVICE_STOP|windows.SERVICE_QUERY_STATUS)
	if err != nil {
		return err
	}
	defer m.Disconnect()
	defer s.Close()
	if _, err := s.Control(svc.Stop); err != nil && !errors.Is(err, windows.ERROR_SERVICE_NOT_ACTIVE) {
		return err
	}
	return waitFor(ctx, s, svc.Stopped)
}

// restartService stops the service if it is stuck part way (paused, or
// starting or stopping), then starts it.
func restartService(ctx context.Context, name string) error {
	if status, _ := serviceStatus(ctx, name); status != "stopped" {
		if err := stopService(ctx, name); err != nil {
			return err
		}
	}
	return startService(ctx, name)
}

// processNames lists each process's executable name, such as notepad.exe.
func processNames() ([][]string, error) {
	snapshot, err := windows.CreateToolhelp32Snapshot(windows.TH32CS_SNAPPROCESS, 0)
	if err != nil {
		return nil, err
	}
	defer windows.CloseHandle(snapshot)
	entry := windows.ProcessEntry32{}
	entry.Size = uint32(unsafe.Sizeof(entry))
	var running [][]string
	for err = windows.Process32First(snapshot, &entry); err == nil; err = windows.Process32Next(snapshot, &entry) {
		running = append(running, []string{windows.UTF16ToString(entry.ExeFile[:])})
	}
	if !errors.Is(err, windows.ERROR_NO_MORE_FILES) {
		return nil, err
	}
	return running, nil
}
