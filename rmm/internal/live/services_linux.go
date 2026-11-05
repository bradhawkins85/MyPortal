package live

import (
	"context"
	"fmt"
	"os/exec"
	"regexp"
	"strings"
	"time"
)

var serviceStartTypes = []StartType{
	{Value: "enabled", Label: "Enabled (starts at boot)"},
	{Value: "disabled", Label: "Disabled"},
}

var unitName = regexp.MustCompile(`^[A-Za-z0-9@._:\\-]+$`)

func systemctl(ctx context.Context, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "systemctl", append([]string{"--no-ask-password", "--no-pager"}, args...)...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		message := strings.TrimSpace(string(out))
		if message == "" {
			message = err.Error()
		}
		return string(out), fmt.Errorf("systemctl %s: %s", args[0], message)
	}
	return string(out), nil
}

func listServices(ctx context.Context) ([]Service, error) {
	units, err := systemctl(ctx, "list-units", "--type=service", "--all", "--no-legend", "--plain")
	if err != nil {
		return nil, err
	}
	files, err := systemctl(ctx, "list-unit-files", "--type=service", "--no-legend", "--plain")
	if err != nil {
		files = ""
	}
	return mergeSystemdServices(units, files), nil
}

// mergeSystemdServices combines loaded units (status) with unit files
// (start setting); unit files not loaded are stopped.
func mergeSystemdServices(units, files string) []Service {
	byName := map[string]*Service{}
	var order []string
	for _, line := range strings.Split(units, "\n") {
		fields := strings.Fields(strings.TrimLeft(line, "●* "))
		if len(fields) < 4 || !strings.HasSuffix(fields[0], ".service") {
			continue
		}
		svc := &Service{
			Name:        fields[0],
			DisplayName: strings.TrimSuffix(fields[0], ".service"),
			Status:      systemdStatus(fields[1], fields[2], fields[3]),
		}
		if len(fields) > 4 {
			svc.Description = strings.Join(fields[4:], " ")
		}
		byName[svc.Name] = svc
		order = append(order, svc.Name)
	}
	for _, line := range strings.Split(files, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 || !strings.HasSuffix(fields[0], ".service") {
			continue
		}
		svc, ok := byName[fields[0]]
		if !ok {
			// Template units (name@.service) only run as instances.
			if strings.HasSuffix(fields[0], "@.service") {
				continue
			}
			svc = &Service{Name: fields[0], DisplayName: strings.TrimSuffix(fields[0], ".service"), Status: "stopped"}
			byName[svc.Name] = svc
			order = append(order, svc.Name)
		}
		svc.StartType = fields[1]
	}
	out := make([]Service, 0, len(order))
	for _, name := range order {
		out = append(out, *byName[name])
	}
	return out
}

func systemdStatus(load, active, sub string) string {
	if load == "not-found" {
		return "missing"
	}
	switch sub {
	case "running":
		return "running"
	case "exited":
		return "exited"
	case "failed":
		return "failed"
	}
	switch active {
	case "activating", "reloading":
		return "starting"
	case "deactivating":
		return "stopping"
	case "failed":
		return "failed"
	case "active":
		return "running"
	}
	return "stopped"
}

func checkUnit(name string) error {
	if !unitName.MatchString(name) || strings.HasPrefix(name, "-") {
		return fmt.Errorf("%q is not a valid systemd unit name", name)
	}
	return nil
}

func controlService(ctx context.Context, name, action string) error {
	if err := checkUnit(name); err != nil {
		return err
	}
	_, err := systemctl(ctx, action, "--", name)
	return err
}

func setServiceStart(ctx context.Context, name, startType string) error {
	if err := checkUnit(name); err != nil {
		return err
	}
	verb := "disable"
	if startType == "enabled" {
		verb = "enable"
	}
	_, err := systemctl(ctx, verb, "--", name)
	return err
}
