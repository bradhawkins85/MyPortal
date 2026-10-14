package thermal

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"
)

const PawnIOVersion = "2.1.0"

// Prepare installs hardware access dependencies on Windows. Call once at
// startup in the background so a failed installation never blocks check-in.
func Prepare(ctx context.Context) error { return preparePlatform(ctx) }

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
