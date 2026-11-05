package live

import (
	"context"
	"errors"
	"sort"
	"strings"
	"unicode"
)

// Service is one row of the service manager.
type Service struct {
	Name        string `json:"name"`
	DisplayName string `json:"display_name"`
	Status      string `json:"status"`
	StartType   string `json:"start_type"`
	Description string `json:"description,omitempty"`
	Account     string `json:"account,omitempty"`
	PID         int    `json:"pid,omitempty"`
}

type serviceArgs struct {
	Name      string `json:"name"`
	Action    string `json:"action"`
	StartType string `json:"start_type"`
}

func validServiceName(name string) error {
	if name == "" || len(name) > 256 || strings.HasPrefix(name, "-") {
		return errors.New("enter a valid service name")
	}
	for _, r := range name {
		if unicode.IsControl(r) {
			return errors.New("enter a valid service name")
		}
	}
	return nil
}

func validStartType(value string) bool {
	for _, item := range serviceStartTypes {
		if item.Value == value {
			return true
		}
	}
	return false
}

func init() {
	register("service.list", func(s *Session, req Request) (interface{}, error) {
		services, err := listServices(s.ctx)
		if err != nil {
			return nil, err
		}
		sort.Slice(services, func(i, j int) bool {
			return strings.ToLower(services[i].DisplayName) < strings.ToLower(services[j].DisplayName)
		})
		return map[string]interface{}{"services": services}, nil
	})
	register("service.control", func(s *Session, req Request) (interface{}, error) {
		var args serviceArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		if err := validServiceName(args.Name); err != nil {
			return nil, err
		}
		switch args.Action {
		case "start", "stop", "restart":
		default:
			return nil, errors.New("action must be start, stop or restart")
		}
		if err := controlService(s.ctx, args.Name, args.Action); err != nil {
			return nil, err
		}
		return serviceState(s.ctx, args.Name)
	})
	register("service.set_start", func(s *Session, req Request) (interface{}, error) {
		var args serviceArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		if err := validServiceName(args.Name); err != nil {
			return nil, err
		}
		if !validStartType(args.StartType) {
			return nil, errors.New("this device does not support that start type")
		}
		if err := setServiceStart(s.ctx, args.Name, args.StartType); err != nil {
			return nil, err
		}
		return serviceState(s.ctx, args.Name)
	})
}

// serviceState is the service as it stands after a change, for the
// browser to update its row.
func serviceState(ctx context.Context, name string) (interface{}, error) {
	if services, err := listServices(ctx); err == nil {
		for _, svc := range services {
			if strings.EqualFold(svc.Name, name) {
				return svc, nil
			}
		}
	}
	return Service{Name: name, DisplayName: name}, nil
}
