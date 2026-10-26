//go:build darwin

package main

import (
	"os/user"
	"strconv"
)

// consoleUserName returns the account signed in at the console; the daemon
// itself runs as root.
func consoleUserName() string {
	uid, err := consoleUID()
	if err != nil {
		return ""
	}
	account, err := user.LookupId(strconv.Itoa(uid))
	if err != nil {
		return ""
	}
	return account.Username
}
