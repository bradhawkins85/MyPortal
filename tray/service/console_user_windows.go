//go:build windows

package main

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// consoleUserName returns the account signed in to the active console
// session. The service runs as SYSTEM, whose USERNAME is the machine account
// (COMPUTERNAME$), so it asks the session's token instead.
func consoleUserName() string {
	sessionID, _, _ := procWTSGetActiveConsole.Call()
	if uint32(sessionID) == 0xFFFFFFFF {
		return ""
	}
	var token windows.Token
	if ret, _, _ := procWTSQueryUserToken.Call(sessionID, uintptr(unsafe.Pointer(&token))); ret == 0 {
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
