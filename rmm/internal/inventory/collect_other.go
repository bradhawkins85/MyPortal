//go:build !windows && !linux && !darwin

package inventory

import "context"

func collectPlatform(ctx context.Context, s *Snapshot) {}
