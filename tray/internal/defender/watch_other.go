//go:build !windows

package defender

import "context"

func WatchDetections(ctx context.Context, changed func()) error { return ErrUnsupported }
