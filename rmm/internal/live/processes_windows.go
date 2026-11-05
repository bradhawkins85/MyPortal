package live

import (
	"sync"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

var procGetProcessMemoryInfo = kernel32.NewProc("K32GetProcessMemoryInfo")

type processMemoryCounters struct {
	CB                         uint32
	PageFaultCount             uint32
	PeakWorkingSetSize         uintptr
	WorkingSetSize             uintptr
	QuotaPeakPagedPoolUsage    uintptr
	QuotaPagedPoolUsage        uintptr
	QuotaPeakNonPagedPoolUsage uintptr
	QuotaNonPagedPoolUsage     uintptr
	PagefileUsage              uintptr
	PeakPagefileUsage          uintptr
}

// The System Idle Process and System cannot be ended.
func protectedSystemPID(pid int) bool { return pid == 0 || pid == 4 }

var accountNames sync.Map

func processUser(handle windows.Handle) string {
	var token windows.Token
	if err := windows.OpenProcessToken(handle, windows.TOKEN_QUERY, &token); err != nil {
		return ""
	}
	defer token.Close()
	tokenUser, err := token.GetTokenUser()
	if err != nil {
		return ""
	}
	sid := tokenUser.User.Sid.String()
	if cached, ok := accountNames.Load(sid); ok {
		return cached.(string)
	}
	name := sid
	if account, domain, _, err := tokenUser.User.Sid.LookupAccount(""); err == nil {
		name = account
		if domain != "" {
			name = domain + `\` + account
		}
	}
	accountNames.Store(sid, name)
	return name
}

func filetimeDuration(ft windows.Filetime) time.Duration {
	return time.Duration(uint64(ft.HighDateTime)<<32|uint64(ft.LowDateTime)) * 100
}

func readProcesses() ([]Process, error) {
	var procs []Process
	err := walkProcesses(func(entry *windows.ProcessEntry32) {
		proc := Process{
			PID:     int(entry.ProcessID),
			PPID:    int(entry.ParentProcessID),
			Name:    windows.UTF16ToString(entry.ExeFile[:]),
			Threads: int(entry.Threads),
		}
		if handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, entry.ProcessID); err == nil {
			var created, exited, kernel, user windows.Filetime
			if windows.GetProcessTimes(handle, &created, &exited, &kernel, &user) == nil {
				proc.cpuTime = filetimeDuration(kernel) + filetimeDuration(user)
				if created.HighDateTime != 0 || created.LowDateTime != 0 {
					started := time.Unix(0, created.Nanoseconds()).UTC()
					proc.Started = &started
				}
			}
			counters := processMemoryCounters{}
			counters.CB = uint32(unsafe.Sizeof(counters))
			if ok, _, _ := procGetProcessMemoryInfo.Call(uintptr(handle), uintptr(unsafe.Pointer(&counters)), uintptr(counters.CB)); ok != 0 {
				proc.MemoryBytes = uint64(counters.WorkingSetSize)
			}
			buf := make([]uint16, windows.MAX_LONG_PATH)
			size := uint32(len(buf))
			if windows.QueryFullProcessImageName(handle, 0, &buf[0], &size) == nil {
				proc.Path = windows.UTF16ToString(buf[:size])
			}
			proc.User = processUser(handle)
			windows.CloseHandle(handle)
		}
		procs = append(procs, proc)
	})
	return procs, err
}

func killProcess(pid int, _ bool) error {
	handle, err := windows.OpenProcess(windows.PROCESS_TERMINATE, false, uint32(pid))
	if err != nil {
		return err
	}
	defer windows.CloseHandle(handle)
	return windows.TerminateProcess(handle, 1)
}
