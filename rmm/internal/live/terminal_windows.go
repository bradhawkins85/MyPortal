package live

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"unsafe"

	"golang.org/x/sys/windows"
)

// terminalShells lists the shells on this device, PowerShell first.
func terminalShells() []string {
	out := []string{}
	if _, err := os.Stat(windowsShellPath("powershell")); err == nil {
		out = append(out, "powershell")
	}
	if _, err := exec.LookPath("pwsh.exe"); err == nil {
		out = append(out, "pwsh")
	}
	if _, err := os.Stat(windowsShellPath("cmd")); err == nil {
		out = append(out, "cmd")
	}
	return out
}

func windowsShellPath(shell string) string {
	system := os.Getenv("SystemRoot")
	if system == "" {
		system = `C:\Windows`
	}
	switch shell {
	case "powershell":
		return filepath.Join(system, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
	case "cmd":
		return filepath.Join(system, "System32", "cmd.exe")
	case "pwsh":
		if path, err := exec.LookPath("pwsh.exe"); err == nil {
			return path
		}
	}
	return ""
}

// conTerminal is a shell attached to a Windows pseudo console (ConPTY,
// Windows 10 1809 and later).
type conTerminal struct {
	console windows.Handle
	process windows.Handle
	input   *os.File
	output  *os.File
	once    sync.Once
	mu      sync.Mutex
	exited  bool
	closed  bool
}

func startTerminal(shell string, cols, rows int) (terminal, error) {
	path := windowsShellPath(shell)
	if path == "" {
		return nil, errors.New("that shell is not installed")
	}
	var inRead, inWrite, outRead, outWrite windows.Handle
	if err := windows.CreatePipe(&inRead, &inWrite, nil, 0); err != nil {
		return nil, err
	}
	if err := windows.CreatePipe(&outRead, &outWrite, nil, 0); err != nil {
		windows.CloseHandle(inRead)
		windows.CloseHandle(inWrite)
		return nil, err
	}
	closeAll := func() {
		for _, h := range []windows.Handle{inRead, inWrite, outRead, outWrite} {
			windows.CloseHandle(h)
		}
	}
	var console windows.Handle
	size := windows.Coord{X: int16(cols), Y: int16(rows)}
	if err := windows.CreatePseudoConsole(size, inRead, outWrite, 0, &console); err != nil {
		closeAll()
		return nil, err
	}
	attrs, err := windows.NewProcThreadAttributeList(1)
	if err != nil {
		windows.ClosePseudoConsole(console)
		closeAll()
		return nil, err
	}
	defer attrs.Delete()
	// The attribute's value is the console handle itself.
	if err := attrs.Update(windows.PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
		*(*unsafe.Pointer)(unsafe.Pointer(&console)), unsafe.Sizeof(console)); err != nil {
		windows.ClosePseudoConsole(console)
		closeAll()
		return nil, err
	}
	info := windows.StartupInfoEx{ProcThreadAttributeList: attrs.List()}
	info.Cb = uint32(unsafe.Sizeof(info))
	info.Flags = windows.STARTF_USESTDHANDLES
	commandLine := windows.EscapeArg(path)
	if shell == "powershell" || shell == "pwsh" {
		commandLine += " -NoLogo"
	}
	cmdLine, err := windows.UTF16PtrFromString(commandLine)
	if err != nil {
		windows.ClosePseudoConsole(console)
		closeAll()
		return nil, err
	}
	dir, _ := windows.UTF16PtrFromString(filepath.Dir(windowsShellPath("cmd")))
	var proc windows.ProcessInformation
	err = windows.CreateProcess(nil, cmdLine, nil, nil, false,
		windows.EXTENDED_STARTUPINFO_PRESENT, nil, dir, &info.StartupInfo, &proc)
	if err != nil {
		windows.ClosePseudoConsole(console)
		closeAll()
		return nil, err
	}
	windows.CloseHandle(proc.Thread)
	// The pseudo console holds its own copies of these ends.
	windows.CloseHandle(inRead)
	windows.CloseHandle(outWrite)
	return &conTerminal{
		console: console,
		process: proc.Process,
		input:   os.NewFile(uintptr(inWrite), "conpty-in"),
		output:  os.NewFile(uintptr(outRead), "conpty-out"),
	}, nil
}

func (t *conTerminal) Read(p []byte) (int, error)  { return t.output.Read(p) }
func (t *conTerminal) Write(p []byte) (int, error) { return t.input.Write(p) }

func (t *conTerminal) Resize(cols, rows int) error {
	return windows.ResizePseudoConsole(t.console, windows.Coord{X: int16(cols), Y: int16(rows)})
}

// Wait returns the shell's exit code. The process handle is released by
// whichever of Wait and Close finishes last, so Close never terminates a
// recycled handle.
func (t *conTerminal) Wait() (int, error) {
	_, waitErr := windows.WaitForSingleObject(t.process, windows.INFINITE)
	t.mu.Lock()
	defer t.mu.Unlock()
	var code uint32
	err := waitErr
	if err == nil {
		err = windows.GetExitCodeProcess(t.process, &code)
	}
	t.exited = true
	if t.closed {
		windows.CloseHandle(t.process)
	}
	if err != nil {
		return -1, err
	}
	return int(code), nil
}

func (t *conTerminal) Close() {
	t.once.Do(func() {
		t.mu.Lock()
		defer t.mu.Unlock()
		if !t.exited {
			_ = windows.TerminateProcess(t.process, 1)
		}
		windows.ClosePseudoConsole(t.console)
		_ = t.input.Close()
		_ = t.output.Close()
		t.closed = true
		if t.exited {
			windows.CloseHandle(t.process)
		}
	})
}
