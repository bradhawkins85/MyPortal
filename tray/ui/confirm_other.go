//go:build !windows && !darwin

package main

import (
	"errors"
	"os/exec"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// confirmAction asks the user a yes/no question with zenity or kdialog and
// reports whether they agreed. Without either, it does not run the action.
func confirmAction(title, question string) bool {
	for _, args := range [][]string{
		{"zenity", "--question", "--title", title, "--text", question},
		{"kdialog", "--title", title, "--yesno", question},
	} {
		if _, err := exec.LookPath(args[0]); err != nil {
			continue
		}
		err := exec.Command(args[0], args[1:]...).Run()
		var exitErr *exec.ExitError
		if err != nil && !errors.As(err, &exitErr) {
			logger.Warn("confirmAction: %s failed: %v", args[0], err)
			return false
		}
		return err == nil
	}
	logger.Warn("confirmAction: no dialog tool (zenity or kdialog) found; not running %q", title)
	showOSNotification(title, "This needs your confirmation, but no dialog could be shown on this computer.")
	return false
}
