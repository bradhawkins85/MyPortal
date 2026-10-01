//go:build !windows

package defender

import "context"

func runPowerShell(context.Context, string) ([]byte, error) { return nil, ErrUnsupported }
