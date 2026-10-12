//go:build !windows && !linux && !darwin

package inventory

import (
	"context"
	"strings"
	"testing"
)

func TestUnsupportedPlatformSoftwareIsUnknown(t *testing.T) {
	snapshot := Snapshot{CPUModel: "Example CPU"}
	collectPlatform(context.Background(), &snapshot)
	if snapshot.Software != nil || len(snapshot.Errors) != 1 || !strings.HasPrefix(snapshot.Errors[0], "software:") {
		t.Fatalf("unsupported software collection = %#v, errors = %v", snapshot.Software, snapshot.Errors)
	}
	if snapshot.CPUModel != "Example CPU" {
		t.Fatal("unsupported software collection discarded available hardware")
	}
}
