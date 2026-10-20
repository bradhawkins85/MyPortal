package thermal

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"
)

// Prepare removes the PawnIO driver that earlier Windows agents installed for
// temperature readings. Systems running those agents reported
// SYSTEM_THREAD_EXCEPTION_NOT_HANDLED (0x7E) crashes, so Windows no longer
// collects temperatures. Call once at startup in the background so a failed
// removal never blocks check-in.
func Prepare(ctx context.Context) error { return preparePlatform(ctx) }

// uninstallArgs returns the program and its arguments from a registered
// uninstall command line, such as `C:\Program Files\PawnIO\uninstall.exe`,
// adding the -silent switch when the registered command does not include it.
func uninstallArgs(command string) (string, string, error) {
	command = strings.TrimSpace(command)
	var program, args string
	if strings.HasPrefix(command, `"`) {
		end := strings.Index(command[1:], `"`)
		if end < 0 {
			return "", "", fmt.Errorf("unterminated quote in uninstall command %q", command)
		}
		program, args = command[1:end+1], command[end+2:]
	} else if end := strings.Index(strings.ToLower(command), ".exe"); end >= 0 {
		program, args = command[:end+4], command[end+4:]
	} else {
		program, args = command, ""
	}
	if program == "" || !strings.HasSuffix(strings.ToLower(program), ".exe") {
		return "", "", fmt.Errorf("unexpected uninstall command %q", command)
	}
	args = strings.TrimSpace(args)
	if !strings.Contains(strings.ToLower(args), "-silent") {
		args = strings.TrimSpace(args + " -silent")
	}
	return program, args, nil
}

// The callbacks keep removal decisions testable without changing a
// machine's driver configuration. uninstallCommand returns "" when PawnIO
// is not registered.
func removePawnIO(ctx context.Context, uninstallCommand func() (string, error), run func(context.Context, string, string) error) error {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return err
	}
	command, err := uninstallCommand()
	if err != nil {
		return fmt.Errorf("reading PawnIO uninstall command: %w", err)
	}
	if command == "" {
		return nil
	}
	program, args, err := uninstallArgs(command)
	if err != nil {
		return err
	}
	if err := run(ctx, program, args); err != nil {
		return fmt.Errorf("uninstalling PawnIO: %w", err)
	}
	command, err = uninstallCommand()
	if err != nil {
		return fmt.Errorf("verifying PawnIO removal: %w", err)
	}
	if command != "" {
		return errors.New("PawnIO is still registered after uninstalling")
	}
	return nil
}
