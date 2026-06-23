//go:build windows

package main

import (
	"os"
	"strings"
	"time"

	"golang.org/x/sys/windows"
)

var singleInstanceMutex windows.Handle

// acquireSingleInstanceLock prevents more than one tray UI process from
// running for the same Windows user. The mutex name includes the current
// user's SID so different users on the same computer can each have their own
// tray icon while duplicate launches for one user exit immediately.
func acquireSingleInstanceLock() (bool, error) {
	name := `Local\MyPortalTrayUI-` + currentUserSIDForMutex()
	namePtr, err := windows.UTF16PtrFromString(name)
	if err != nil {
		return false, err
	}

	// During a deliberate self-restart the replacement process can start before
	// the old process has fully unwound systray.Run and released its mutex. Treat
	// that narrow overlap as expected and wait briefly instead of exiting as a
	// duplicate, otherwise a config/icon refresh can leave no UI process running.
	deadline := time.Now()
	if os.Getenv("MYPORTAL_TRAY_RESTART") == "1" {
		deadline = deadline.Add(10 * time.Second)
	}

	for {
		handle, err := windows.CreateMutex(nil, true, namePtr)
		if err == windows.ERROR_ALREADY_EXISTS {
			if handle != 0 {
				_ = windows.CloseHandle(handle)
			}
			if time.Now().Before(deadline) {
				time.Sleep(200 * time.Millisecond)
				continue
			}
			return false, nil
		}
		if err != nil {
			return false, err
		}

		singleInstanceMutex = handle
		return true, nil
	}
}

func releaseSingleInstanceLock() {
	if singleInstanceMutex == 0 {
		return
	}
	_ = windows.ReleaseMutex(singleInstanceMutex)
	_ = windows.CloseHandle(singleInstanceMutex)
	singleInstanceMutex = 0
}

func currentUserSIDForMutex() string {
	token := windows.Token(0)
	if err := windows.OpenProcessToken(windows.CurrentProcess(), windows.TOKEN_QUERY, &token); err == nil {
		defer token.Close()
		if tokenUser, err := token.GetTokenUser(); err == nil && tokenUser.User.Sid != nil {
			return sanitizeMutexComponent(tokenUser.User.Sid.String())
		}
	}

	// Fallback only if the token/SID lookup fails. Keeping the name in the Local
	// namespace still prevents duplicate UI instances within this logon session.
	return "session"
}

func sanitizeMutexComponent(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return "unknown"
	}
	replacer := strings.NewReplacer(`\`, "-", `/`, "-", `:`, "-", `*`, "-", `?`, "-", `"`, "-", `<`, "-", `>`, "-", `|`, "-")
	return replacer.Replace(value)
}
