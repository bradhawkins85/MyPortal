package checks

import (
	"errors"
	"testing"

	"github.com/bradhawkins85/myportal-rmm/internal/inventory"
)

func TestProgramStatuses(t *testing.T) {
	programs := []inventory.Program{{Name: "Google Chrome", Version: "130"},
		{Name: "google chrome", Version: "131"}, {Name: "Google Chrome", Version: "130"},
		{Name: "Google Chrome Beta", Version: "132"}}
	results := programStatuses([]string{"GOOGLE CHROME", "Google", "Missing"}, programs, nil)
	if !results[0].Installed || len(results[0].Versions) != 2 || results[0].Versions[0] != "130" {
		t.Fatalf("versions and case matching: %+v", results[0])
	}
	if results[1].Installed || results[2].Installed {
		t.Fatalf("exact matching: %+v", results)
	}
	results = programStatuses([]string{"Google Chrome"}, nil, errors.New("access denied"))
	if results[0].Error != "access denied" {
		t.Fatalf("lost collection failure: %+v", results[0])
	}
	results = programStatuses([]string{"Google Chrome"}, []inventory.Program{{Name: "Google Chrome"}}, nil)
	if !results[0].Installed || len(results[0].Versions) != 1 || results[0].Versions[0] != "" {
		t.Fatalf("unknown version must remain distinct from missing: %+v", results[0])
	}
}
