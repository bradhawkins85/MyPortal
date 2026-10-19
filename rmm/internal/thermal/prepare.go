package thermal

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"
)

const PawnIOVersion = "2.1.0"

// Prepare installs hardware access dependencies on physical Windows machines.
// Call once at startup in the background so a failed installation never blocks check-in.
func Prepare(ctx context.Context) error { return preparePlatform(ctx) }

// Match guest hardware identifiers, not HypervisorPresent: physical hosts with
// Hyper-V or virtualization-based security still have real thermal sensors.
func isVirtualMachine(manufacturer, model string) bool {
	identity := strings.ToLower(manufacturer + " " + model)
	for _, marker := range []string{
		"virtual machine", "vmware", "virtualbox", "innotek", "kvm", "qemu",
		"xen", "hvm domu", "parallels", "bhyve", "bochs", "openstack", "nutanix",
		"google compute engine", "alibaba cloud",
	} {
		if strings.Contains(identity, marker) {
			return true
		}
	}
	// EC2 bare-metal instances expose physical hardware and can have sensors.
	return strings.Contains(identity, "amazon ec2") && !strings.Contains(strings.ToLower(model), ".metal")
}

func preparePhysicalMachine(ctx context.Context, identify func() (string, string, error), prepare func(context.Context) error) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	manufacturer, model, err := identify()
	if err != nil {
		return fmt.Errorf("identifying hardware before PawnIO setup: %w", err)
	}
	if isVirtualMachine(manufacturer, model) {
		return nil
	}
	if strings.TrimSpace(manufacturer) == "" || strings.TrimSpace(model) == "" {
		return fmt.Errorf("identifying hardware before PawnIO setup: missing manufacturer or model")
	}
	return prepare(ctx)
}

func compatiblePawnIO(version string) bool {
	parts := strings.Split(version, ".")
	if len(parts) > 4 {
		return false
	}
	var numbers [4]int
	for i, part := range parts {
		number, err := strconv.Atoi(part)
		if err != nil || number < 0 {
			return false
		}
		numbers[i] = number
	}
	return numbers[0] > 2 || numbers[0] == 2 && numbers[1] >= 1
}

// The callbacks keep installation decisions testable without changing a
// machine's driver configuration. Registry read failures must not cause an
// existing installation to be overwritten.
func preparePawnIO(ctx context.Context, version func() (string, error), install func(context.Context) error) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return err
	}
	installed, err := version()
	if err != nil {
		return fmt.Errorf("reading installed PawnIO version: %w", err)
	}
	if compatiblePawnIO(installed) {
		return nil
	}
	if err := install(ctx); err != nil {
		return fmt.Errorf("installing bundled PawnIO %s: %w", PawnIOVersion, err)
	}
	installed, err = version()
	if err != nil {
		return fmt.Errorf("verifying installed PawnIO version: %w", err)
	}
	if !compatiblePawnIO(installed) {
		return fmt.Errorf("PawnIO installer did not register version %s or newer", PawnIOVersion)
	}
	return nil
}
