//go:build linux

package inventory

import (
	"context"
	"os"
	"path/filepath"
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

func TestInstalledProgramsExcludeRemovedDpkgPackages(t *testing.T) {
	directory := t.TempDir()
	script := "#!/bin/sh\nprintf 'installed\tapp\t1.2\tPublisher\nconfig-files\tremoved\t1.0\tPublisher\n'\n"
	if err := os.WriteFile(filepath.Join(directory, "dpkg-query"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", directory)
	programs, err := InstalledPrograms(context.Background())
	if err != nil || len(programs) != 1 || programs[0].Name != "app" || programs[0].Version != "1.2" {
		t.Fatalf("installed packages: %+v, error: %v", programs, err)
	}
}
