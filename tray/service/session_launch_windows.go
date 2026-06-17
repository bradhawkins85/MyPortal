//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"unsafe"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"golang.org/x/sys/windows"
)

var (
	kernel32                = syscall.NewLazyDLL("kernel32.dll")
	wtsapi32                = syscall.NewLazyDLL("wtsapi32.dll")
	userenv                 = syscall.NewLazyDLL("userenv.dll")
	procWTSGetActiveConsole = kernel32.NewProc("WTSGetActiveConsoleSessionId")
	procWTSQueryUserToken   = wtsapi32.NewProc("WTSQueryUserToken")
	procCreateEnvironment   = userenv.NewProc("CreateEnvironmentBlock")
	procDestroyEnvironment  = userenv.NewProc("DestroyEnvironmentBlock")
)

const (
	tokenAssignPrimary    = 0x0001
	tokenDuplicate        = 0x0002
	tokenImpersonate      = 0x0004
	tokenQuery            = 0x0008
	tokenAdjustDefault    = 0x0080
	tokenAdjustSessionID  = 0x0100
	tokenAllAccess        = tokenAssignPrimary | tokenDuplicate | tokenImpersonate | tokenQuery | tokenAdjustDefault | tokenAdjustSessionID
	securityImpersonation = 2
	tokenPrimary          = 1
	createNoWindow        = 0x08000000
	createUnicodeEnv      = 0x00000400
)

func launchTrayUIForActiveUser() error {
	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("resolve service executable: %w", err)
	}
	uiPath := filepath.Join(filepath.Dir(exe), "myportal-tray-ui.exe")
	if _, err := os.Stat(uiPath); err != nil {
		return fmt.Errorf("tray UI executable %q: %w", uiPath, err)
	}

	sessionID, _, _ := procWTSGetActiveConsole.Call()
	if uint32(sessionID) == 0xFFFFFFFF {
		return fmt.Errorf("no active console session")
	}

	var userToken windows.Token
	ret, _, callErr := procWTSQueryUserToken.Call(sessionID, uintptr(unsafe.Pointer(&userToken)))
	if ret == 0 {
		return fmt.Errorf("WTSQueryUserToken session %d: %w", sessionID, callErr)
	}
	defer userToken.Close()

	var primaryToken windows.Token
	if err := windows.DuplicateTokenEx(
		userToken,
		tokenAllAccess,
		nil,
		securityImpersonation,
		tokenPrimary,
		&primaryToken,
	); err != nil {
		return fmt.Errorf("DuplicateTokenEx: %w", err)
	}
	defer primaryToken.Close()

	var env uintptr
	ret, _, callErr = procCreateEnvironment.Call(uintptr(unsafe.Pointer(&env)), uintptr(primaryToken), 0)
	if ret == 0 {
		return fmt.Errorf("CreateEnvironmentBlock: %w", callErr)
	}
	defer procDestroyEnvironment.Call(env)

	cmdLine, err := syscall.UTF16PtrFromString(`"` + uiPath + `"`)
	if err != nil {
		return fmt.Errorf("build command line: %w", err)
	}
	desktop, err := syscall.UTF16PtrFromString(`winsta0\default`)
	if err != nil {
		return fmt.Errorf("build desktop name: %w", err)
	}
	workingDir, err := syscall.UTF16PtrFromString(filepath.Dir(uiPath))
	if err != nil {
		return fmt.Errorf("build working directory: %w", err)
	}

	var si syscall.StartupInfo
	si.Cb = uint32(unsafe.Sizeof(si))
	si.Desktop = desktop
	var pi syscall.ProcessInformation
	if err := syscall.CreateProcessAsUser(
		syscall.Token(primaryToken),
		nil,
		cmdLine,
		nil,
		nil,
		false,
		createNoWindow|createUnicodeEnv,
		(*uint16)(unsafe.Pointer(env)),
		workingDir,
		&si,
		&pi,
	); err != nil {
		return fmt.Errorf("CreateProcessAsUser: %w", err)
	}
	_ = syscall.CloseHandle(pi.Thread)
	_ = syscall.CloseHandle(pi.Process)
	logger.Info("Launched MyPortal tray UI in active user session %d", sessionID)
	return nil
}
