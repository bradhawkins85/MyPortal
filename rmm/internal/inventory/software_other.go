//go:build !windows && !linux

package inventory

import (
	"context"
	"errors"
)

func InstalledPrograms(ctx context.Context) ([]Program, error) {
	return nil, errors.New("installed software checks are unsupported on this platform")
}
