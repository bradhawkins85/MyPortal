package checks

import (
	"strings"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

// CheckProcesses counts the running processes with each name, ignoring case
// and a trailing .exe.
func CheckProcesses(names []string) []client.ProcessStatus {
	running, err := processNames()
	statuses := make([]client.ProcessStatus, len(names))
	for i, name := range names {
		statuses[i].Name = name
		if err != nil {
			statuses[i].Error = err.Error()
			continue
		}
		statuses[i].Count = countProcesses(running, name)
	}
	return statuses
}

func processKey(name string) string {
	return strings.TrimSuffix(strings.ToLower(strings.TrimSpace(name)), ".exe")
}

// countProcesses counts the processes going by name; each entry in running
// is the names one process goes by.
func countProcesses(running [][]string, name string) int {
	want := processKey(name)
	if want == "" {
		return 0
	}
	count := 0
	for _, names := range running {
		for _, candidate := range names {
			if processKey(candidate) == want {
				count++
				break
			}
		}
	}
	return count
}
