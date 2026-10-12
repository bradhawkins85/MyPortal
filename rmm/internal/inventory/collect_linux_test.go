//go:build linux

package inventory

import (
	"context"
	"strings"
	"testing"
)

func TestLinuxPackagesUnavailableIsUnknown(t *testing.T) {
	// An empty PATH models distributions without dpkg-query, rpm or pacman.
	t.Setenv("PATH", t.TempDir())
	snapshot := Snapshot{CPUModel: "Example CPU", RAMBytes: 8 << 30}
	snapshot.Software = linuxPackages(context.Background(), &snapshot)
	if snapshot.Software != nil || len(snapshot.Errors) != 1 || !strings.HasPrefix(snapshot.Errors[0], "software:") {
		t.Fatalf("unavailable software collection = %#v, errors = %v", snapshot.Software, snapshot.Errors)
	}
	if snapshot.CPUModel != "Example CPU" || snapshot.RAMBytes != 8<<30 {
		t.Fatal("unavailable software collection discarded available hardware")
	}
}
