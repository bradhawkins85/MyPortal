//go:build linux

package inventory

import (
	"context"
	"errors"
	"strings"
)

// InstalledPrograms collects only software, without querying hardware.
func InstalledPrograms(ctx context.Context) ([]Program, error) {
	snapshot := &Snapshot{}
	programs := linuxPackages(ctx, snapshot)
	if len(snapshot.Errors) > 0 {
		return nil, errors.New(strings.Join(snapshot.Errors, "; "))
	}
	return programs, nil
}
