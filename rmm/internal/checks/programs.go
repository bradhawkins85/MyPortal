package checks

import (
	"context"
	"sort"
	"strings"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
	"github.com/bradhawkins85/myportal-rmm/internal/inventory"
)

// CheckPrograms collects software once for all watched names. Collection errors
// must never be reported as a missing application.
func CheckPrograms(ctx context.Context, names []string) []client.ProgramStatus {
	programs, err := inventory.InstalledPrograms(ctx)
	return programStatuses(names, programs, err)
}

func programStatuses(names []string, programs []inventory.Program, err error) []client.ProgramStatus {
	results := make([]client.ProgramStatus, 0, len(names))
	for _, name := range names {
		result := client.ProgramStatus{Name: name, Versions: []string{}}
		if err != nil {
			result.Error = err.Error()
		} else {
			versions := map[string]bool{}
			for _, program := range programs {
				if strings.EqualFold(strings.TrimSpace(program.Name), strings.TrimSpace(name)) {
					result.Installed = true
					versions[strings.TrimSpace(program.Version)] = true
				}
			}
			for version := range versions {
				result.Versions = append(result.Versions, version)
			}
			sort.Strings(result.Versions)
			if len(result.Versions) > 100 {
				result.Versions = result.Versions[:100]
				result.Error = "too many installed versions to check"
			}
		}
		results = append(results, result)
	}
	return results
}
