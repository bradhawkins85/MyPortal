//go:build !windows && !linux && !darwin

package inventory

import (
	"context"
	"errors"
)

func collectPlatform(_ context.Context, s *Snapshot) {
	s.fail("software", errors.New("installed software inventory is unsupported on this platform"))
}
