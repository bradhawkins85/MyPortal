//go:build !windows

package rmminstall

import "context"

func platformServiceNotInstalled(err error) bool { return false }

func waitServiceRemoved(ctx context.Context) error { return nil }
