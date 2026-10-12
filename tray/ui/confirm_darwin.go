//go:build darwin

package main

import (
	"os/exec"
)

// confirmAction asks the user a yes/no question and reports whether they
// chose Run. The text is passed as script arguments, so it needs no quoting.
func confirmAction(title, question string) bool {
	cmd := exec.Command("osascript",
		"-e", "on run argv",
		"-e", `display dialog (item 1 of argv) with title (item 2 of argv) buttons {"Cancel", "Run"} default button "Run" cancel button "Cancel" with icon note`,
		"-e", "end run",
		question, title)
	// osascript exits non-zero when the user presses Cancel.
	return cmd.Run() == nil
}
