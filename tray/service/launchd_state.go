// launchd_state.go reads the output of "launchctl print" for the macOS tray UI
// LaunchAgent. It has no build tag so the parsing can be unit tested on every
// platform.
package main

import (
	"bufio"
	"strings"
)

// launchdJobState is the part of a "launchctl print" report the UI watchdog
// needs.
type launchdJobState struct {
	Running     bool
	NeverExited bool
}

// parseLaunchdJobState reads the top-level "state" and "last exit code"
// fields. Nested dictionaries (endpoints, environment, ...) are indented
// further and never contain these keys at the job level.
func parseLaunchdJobState(out string) launchdJobState {
	var st launchdJobState
	scanner := bufio.NewScanner(strings.NewReader(out))
	for scanner.Scan() {
		key, value, ok := strings.Cut(strings.TrimSpace(scanner.Text()), " = ")
		if !ok {
			continue
		}
		switch key {
		case "state":
			if !st.Running {
				st.Running = value == "running"
			}
		case "last exit code":
			st.NeverExited = strings.Contains(value, "never exited")
		}
	}
	return st
}
