//go:build !linux && !windows

package thermal

import "context"

func collectPlatform(_ context.Context, s *Snapshot) { s.Status = "unsupported" }
