//go:build !linux && !darwin && !windows

package overseer

import (
	"context"
	"errors"
)

type unsupportedController struct{}

// NewController returns a controller that sees no services.
func NewController() Controller { return unsupportedController{} }

func (unsupportedController) Status(context.Context, Service) (State, error) { return State{}, nil }
func (unsupportedController) Start(context.Context, Service) error {
	return errors.ErrUnsupported
}
func (unsupportedController) Restart(context.Context, Service) error {
	return errors.ErrUnsupported
}
