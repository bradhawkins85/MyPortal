//go:build !windows

package thermal

import "context"

func preparePlatform(_ context.Context) error { return nil }
