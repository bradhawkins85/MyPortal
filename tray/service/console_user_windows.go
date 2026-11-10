//go:build windows

package main

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// consoleUserName returns the account in use on the device now. The service
// runs as SYSTEM, whose USERNAME is the machine account (COMPUTERNAME$), so
// it reads each session's token instead. Users left signed in but switched
// away from or disconnected are skipped, so the person actually at the
// machine (or the active remote session) is reported.
func consoleUserName() string {
	consoleID, _, _ := procWTSGetActiveConsole.Call()
	var info *windows.WTS_SESSION_INFO
	var count uint32
	if err := windows.WTSEnumerateSessions(0, 0, 1, &info, &count); err != nil {
		return ""
	}
	defer windows.WTSFreeMemory(uintptr(unsafe.Pointer(info)))
	sessions := make([]consoleSession, 0, count)
	for _, s := range unsafe.Slice(info, count) {
		if s.State != windows.WTSActive {
			continue
		}
		sessions = append(sessions, consoleSession{
			ID: s.SessionID, Active: true, User: sessionUserName(s.SessionID),
		})
	}
	return pickConsoleUser(sessions, uint32(consoleID))
}

func sessionUserName(sessionID uint32) string {
	var token windows.Token
	if err := windows.WTSQueryUserToken(sessionID, &token); err != nil {
		return ""
	}
	defer token.Close()
	tokenUser, err := token.GetTokenUser()
	if err != nil {
		return ""
	}
	account, _, _, err := tokenUser.User.Sid.LookupAccount("")
	if err != nil {
		return ""
	}
	return account
}
