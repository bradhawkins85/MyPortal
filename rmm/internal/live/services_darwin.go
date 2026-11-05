package live

import (
	"context"
	"errors"
	"fmt"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// launchd jobs have no start setting the agent changes.
var serviceStartTypes = []StartType{}

var launchdLabel = regexp.MustCompile(`^[A-Za-z0-9._-]+$`)

func launchctl(ctx context.Context, args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "/bin/launchctl", args...).CombinedOutput()
	if err != nil {
		message := strings.TrimSpace(string(out))
		if message == "" {
			message = err.Error()
		}
		return string(out), fmt.Errorf("launchctl %s: %s", args[0], message)
	}
	return string(out), nil
}

func listServices(ctx context.Context) ([]Service, error) {
	out, err := launchctl(ctx, "list")
	if err != nil {
		return nil, err
	}
	return parseLaunchctlList(out), nil
}

func parseLaunchctlList(text string) []Service {
	var out []Service
	for i, line := range strings.Split(text, "\n") {
		fields := strings.Split(line, "\t")
		if i == 0 || len(fields) < 3 {
			continue
		}
		svc := Service{Name: fields[2], DisplayName: fields[2], Status: "stopped", StartType: "launchd"}
		if pid, err := strconv.Atoi(fields[0]); err == nil {
			svc.PID = pid
			svc.Status = "running"
		} else if fields[1] != "0" && fields[1] != "-" {
			svc.Status = "failed"
			svc.Description = "Last exit status " + fields[1]
		}
		out = append(out, svc)
	}
	return out
}

func controlService(ctx context.Context, name, action string) error {
	if !launchdLabel.MatchString(name) {
		return fmt.Errorf("%q is not a valid launchd label", name)
	}
	target := "system/" + name
	var err error
	switch action {
	case "start":
		_, err = launchctl(ctx, "kickstart", target)
	case "restart":
		_, err = launchctl(ctx, "kickstart", "-k", target)
	case "stop":
		_, err = launchctl(ctx, "kill", "SIGTERM", target)
	}
	return err
}

func setServiceStart(context.Context, string, string) error {
	return errors.New("changing how launchd jobs start is not supported")
}
