package overseer

import (
	"bufio"
	"bytes"
	"context"
	"os/exec"
	"runtime"
	"strings"
)

// Service names. kardianos/service names the RMM agent's launchd job and
// systemd unit after the service; the tray's macOS LaunchDaemon ships in the pkg.
const (
	trayServiceName = "MyPortalTrayService"
	rmmServiceName  = "MyPortalRMMAgent"
	trayLaunchLabel = "io.myportal.tray.service"
)

// DefaultServices lists the tray service and RMM agent on this platform.
func DefaultServices() []Service {
	switch runtime.GOOS {
	case "darwin":
		return []Service{
			{Key: ServiceTray, Name: trayLaunchLabel, Plist: "/Library/LaunchDaemons/" + trayLaunchLabel + ".plist"},
			{Key: ServiceRMM, Name: rmmServiceName, Plist: "/Library/LaunchDaemons/" + rmmServiceName + ".plist"},
		}
	case "linux":
		return []Service{
			{Key: ServiceTray, Name: trayServiceName + ".service"},
			{Key: ServiceRMM, Name: rmmServiceName + ".service"},
		}
	default:
		return []Service{
			{Key: ServiceTray, Name: trayServiceName},
			{Key: ServiceRMM, Name: rmmServiceName},
		}
	}
}

// commandRunner runs a command and returns its combined output.
type commandRunner func(ctx context.Context, name string, args ...string) ([]byte, error)

func runCommand(ctx context.Context, name string, args ...string) ([]byte, error) {
	return exec.CommandContext(ctx, name, args...).CombinedOutput()
}

// parseSystemctlShow reads `systemctl show --property=LoadState,ActiveState,UnitFileState`.
func parseSystemctlShow(out []byte) State {
	props := map[string]string{}
	sc := bufio.NewScanner(bytes.NewReader(out))
	for sc.Scan() {
		if k, v, ok := strings.Cut(strings.TrimSpace(sc.Text()), "="); ok {
			props[k] = v
		}
	}
	load := props["LoadState"]
	st := State{Installed: load == "loaded" || load == "masked"}
	switch props["UnitFileState"] {
	case "disabled", "masked", "masked-runtime", "":
		st.Enabled = false
	default:
		st.Enabled = load != "masked"
	}
	switch props["ActiveState"] {
	// activating covers systemd's own restart-after-failure wait.
	case "active", "reloading", "activating", "deactivating":
		st.Running = true
	}
	return st
}

// parseLaunchctlState reads the job state from `launchctl print system/<label>`.
func parseLaunchctlState(out []byte) bool {
	sc := bufio.NewScanner(bytes.NewReader(out))
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if k, v, ok := strings.Cut(line, "="); ok && strings.TrimSpace(k) == "state" {
			return strings.TrimSpace(v) == "running"
		}
	}
	return false
}

// launchctlDisabled reports whether `launchctl print-disabled system` lists
// label as disabled. Older macOS prints `=> true`, newer `=> disabled`.
func launchctlDisabled(out []byte, label string) bool {
	sc := bufio.NewScanner(bytes.NewReader(out))
	quoted := `"` + label + `"`
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		k, v, ok := strings.Cut(line, "=>")
		if !ok || strings.TrimSpace(k) != quoted {
			continue
		}
		v = strings.TrimSpace(v)
		return v == "true" || v == "disabled"
	}
	return false
}
