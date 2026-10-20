//go:build windows

package runner

import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"

	"golang.org/x/sys/windows"
)

// loggedInUser is the user signed in to an active Windows session.
type loggedInUser struct {
	token windows.Token
	sid   *windows.SID
}

func (r *Runner) findUser() (*loggedInUser, error) {
	token, err := activeSessionToken()
	if err != nil {
		return nil, err
	}
	tokenUser, err := token.GetTokenUser()
	if err != nil {
		token.Close()
		return nil, err
	}
	sid, err := tokenUser.User.Sid.Copy()
	if err != nil {
		token.Close()
		return nil, err
	}
	return &loggedInUser{token: token, sid: sid}, nil
}

// activeSessionToken returns the token of the user at the console, or of the
// first active Remote Desktop session when nobody is at the console.
func activeSessionToken() (windows.Token, error) {
	candidates := []uint32{}
	if console := windows.WTSGetActiveConsoleSessionId(); console != 0xFFFFFFFF {
		candidates = append(candidates, console)
	}
	var sessions *windows.WTS_SESSION_INFO
	var count uint32
	if err := windows.WTSEnumerateSessions(0, 0, 1, &sessions, &count); err == nil {
		for _, session := range unsafe.Slice(sessions, count) {
			if session.State == windows.WTSActive {
				candidates = append(candidates, session.SessionID)
			}
		}
		windows.WTSFreeMemory(uintptr(unsafe.Pointer(sessions)))
	}
	for _, id := range candidates {
		var token windows.Token
		if err := windows.WTSQueryUserToken(id, &token); err == nil {
			return token, nil
		}
	}
	return 0, ErrNoLoggedInUser
}

func (u *loggedInUser) Close() {
	u.token.Close()
}

func (u *loggedInUser) workDir(agentDir string) string {
	return agentDir
}

// grant gives the user full control of the run's working folder and,
// through inheritance, the files in it.
func (u *loggedInUser) grant(dir string) error {
	sd, err := windows.GetNamedSecurityInfo(dir, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return err
	}
	current, _, err := sd.DACL()
	if err != nil {
		return err
	}
	acl, err := windows.ACLFromEntries([]windows.EXPLICIT_ACCESS{{
		AccessPermissions: windows.GENERIC_ALL,
		AccessMode:        windows.GRANT_ACCESS,
		Inheritance:       windows.SUB_CONTAINERS_AND_OBJECTS_INHERIT,
		Trustee: windows.TRUSTEE{
			TrusteeForm:  windows.TRUSTEE_IS_SID,
			TrusteeType:  windows.TRUSTEE_IS_USER,
			TrusteeValue: windows.TrusteeValueFromSID(u.sid),
		},
	}}, current)
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(dir, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION, nil, nil, acl, nil)
}

// environ is the user's own environment, as Explorer would give it.
func (u *loggedInUser) environ() []string {
	env, err := u.token.Environ(false)
	if err != nil {
		return os.Environ()
	}
	return env
}

func environmentBlock(env []string) *uint16 {
	sorted := append([]string(nil), env...)
	sort.SliceStable(sorted, func(i, j int) bool { return strings.ToUpper(sorted[i]) < strings.ToUpper(sorted[j]) })
	var block []uint16
	for _, item := range sorted {
		if strings.ContainsRune(item, 0) {
			continue
		}
		block = append(block, utf16.Encode([]rune(item))...)
		block = append(block, 0)
	}
	block = append(block, 0)
	return &block[0]
}

// pipe returns an inheritable write handle for the child and copies what the
// child writes into out until the child and its descendants close it.
func pipe(out io.Writer, copied *sync.WaitGroup) (windows.Handle, *os.File, error) {
	var read, write windows.Handle
	sa := windows.SecurityAttributes{Length: uint32(unsafe.Sizeof(windows.SecurityAttributes{})), InheritHandle: 1}
	if err := windows.CreatePipe(&read, &write, &sa, 0); err != nil {
		return 0, nil, err
	}
	if err := windows.SetHandleInformation(read, windows.HANDLE_FLAG_INHERIT, 0); err != nil {
		windows.CloseHandle(read)
		windows.CloseHandle(write)
		return 0, nil, err
	}
	reader := os.NewFile(uintptr(read), "|0")
	copied.Add(1)
	go func() {
		defer copied.Done()
		_, _ = io.Copy(out, reader)
	}()
	return write, reader, nil
}

// run starts the command in the user's session on the interactive desktop.
// Go's os/exec cannot name a desktop, and without one the process would be
// left on the service's hidden desktop, which the user cannot use.
func (u *loggedInUser) run(ctx context.Context, spec launch) (*int, error) {
	var copied sync.WaitGroup
	stdoutWrite, stdoutRead, err := pipe(spec.stdout, &copied)
	if err != nil {
		return nil, err
	}
	defer stdoutRead.Close()
	stderrWrite, stderrRead, err := pipe(spec.stderr, &copied)
	if err != nil {
		windows.CloseHandle(stdoutWrite)
		return nil, err
	}
	defer stderrRead.Close()
	nul, err := windows.UTF16PtrFromString("NUL")
	if err != nil {
		return nil, err
	}
	sa := windows.SecurityAttributes{Length: uint32(unsafe.Sizeof(windows.SecurityAttributes{})), InheritHandle: 1}
	stdin, err := windows.CreateFile(nul, windows.GENERIC_READ, windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE, &sa, windows.OPEN_EXISTING, 0, 0)
	if err != nil {
		windows.CloseHandle(stdoutWrite)
		windows.CloseHandle(stderrWrite)
		return nil, err
	}
	handles := []windows.Handle{stdin, stdoutWrite, stderrWrite}
	closeChildEnds := func() {
		for _, handle := range handles {
			windows.CloseHandle(handle)
		}
	}
	// Only these handles reach the script, never other runs' pipes.
	attributes, err := windows.NewProcThreadAttributeList(1)
	if err != nil {
		closeChildEnds()
		return nil, err
	}
	defer attributes.Delete()
	if err := attributes.Update(windows.PROC_THREAD_ATTRIBUTE_HANDLE_LIST, unsafe.Pointer(&handles[0]), uintptr(len(handles))*unsafe.Sizeof(handles[0])); err != nil {
		closeChildEnds()
		return nil, err
	}
	desktop, _ := windows.UTF16PtrFromString(`winsta0\default`)
	si := windows.StartupInfoEx{
		StartupInfo: windows.StartupInfo{
			Desktop:    desktop,
			Flags:      windows.STARTF_USESTDHANDLES | windows.STARTF_USESHOWWINDOW,
			ShowWindow: windows.SW_HIDE,
			StdInput:   stdin,
			StdOutput:  stdoutWrite,
			StdErr:     stderrWrite,
		},
		ProcThreadAttributeList: attributes.List(),
	}
	si.Cb = uint32(unsafe.Sizeof(si))
	commandLine, err := windows.UTF16PtrFromString(windows.ComposeCommandLine(append([]string{spec.name}, spec.args...)))
	if err != nil {
		closeChildEnds()
		return nil, err
	}
	application, err := windows.UTF16PtrFromString(spec.name)
	if err != nil {
		closeChildEnds()
		return nil, err
	}
	dir, err := windows.UTF16PtrFromString(spec.dir)
	if err != nil {
		closeChildEnds()
		return nil, err
	}
	var pi windows.ProcessInformation
	flags := uint32(windows.CREATE_UNICODE_ENVIRONMENT | windows.EXTENDED_STARTUPINFO_PRESENT | windows.CREATE_NO_WINDOW)
	err = windows.CreateProcessAsUser(u.token, application, commandLine, nil, nil, true, flags,
		environmentBlock(spec.env), dir, &si.StartupInfo, &pi)
	// The script holds its own copies now; closing ours lets the pipes end.
	closeChildEnds()
	if err != nil {
		return nil, err
	}
	windows.CloseHandle(pi.Thread)
	defer windows.CloseHandle(pi.Process)

	exited := make(chan struct{})
	go func() {
		defer close(exited)
		_, _ = windows.WaitForSingleObject(pi.Process, windows.INFINITE)
	}()
	select {
	case <-exited:
	case <-ctx.Done():
		kill := exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(int(pi.ProcessId)))
		kill.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
		if kill.Run() != nil {
			_ = windows.TerminateProcess(pi.Process, 1)
		}
		<-exited
	}
	// Like exec.Cmd.WaitDelay: stop reading if something the script started
	// keeps its output open after it exits.
	drained := make(chan struct{})
	go func() {
		copied.Wait()
		close(drained)
	}()
	select {
	case <-drained:
	case <-time.After(10 * time.Second):
		stdoutRead.Close()
		stderrRead.Close()
		<-drained
	}
	var code uint32
	if err := windows.GetExitCodeProcess(pi.Process, &code); err != nil {
		return nil, errors.New("could not read the script's exit code: " + err.Error())
	}
	exitCode := int(code)
	return &exitCode, nil
}
