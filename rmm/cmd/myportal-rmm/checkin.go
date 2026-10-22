package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/checks"
	"github.com/bradhawkins85/myportal-rmm/internal/client"
	"github.com/bradhawkins85/myportal-rmm/internal/eventlog"
	"github.com/bradhawkins85/myportal-rmm/internal/inventory"
	"github.com/bradhawkins85/myportal-rmm/internal/runner"
	"github.com/bradhawkins85/myportal-rmm/internal/state"
	"github.com/bradhawkins85/myportal-rmm/internal/thermal"
)

var eventCollectionMu sync.Mutex
var fullCheckinMu sync.Mutex

// Routine collection never blocks the command long-poll. Manual check-ins run
// as separately audited jobs and never reset any periodic reporting timers.
func checkinLoop(ctx context.Context, api *client.Client, r *runner.Runner) {
	for ctx.Err() == nil {
		report := details(r)
		report.Thermal = thermal.Collect(ctx)
		wait := checkinEvery
		if err := api.Checkin(ctx, report); err != nil {
			log.Printf("check-in failed: %v", err)
			wait = 15 * time.Minute
		}
		sleep(ctx, wait)
	}
}

type checkinStep struct {
	name string
	run  func(context.Context) error
}

func runCheckinSteps(ctx context.Context, steps []checkinStep) client.Result {
	code := 0
	var output, failures []string
	for _, step := range steps {
		err := ctx.Err()
		if err == nil {
			err = step.run(ctx)
		}
		if err != nil {
			code = 1
			failures = append(failures, fmt.Sprintf("%s: %v", step.name, err))
		} else {
			output = append(output, step.name+": complete")
		}
	}
	return client.Result{ExitCode: &code, Stdout: strings.Join(output, "\n"), Stderr: strings.Join(failures, "\n"),
		TimedOut: errors.Is(ctx.Err(), context.DeadlineExceeded)}
}

func performFullCheckin(ctx context.Context, api *client.Client, r *runner.Runner) client.Result {
	if !fullCheckinMu.TryLock() {
		code := 1
		return client.Result{ExitCode: &code, Error: "A full check-in is already running on this device."}
	}
	defer fullCheckinMu.Unlock()
	ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
	defer cancel()
	return runCheckinSteps(ctx, []checkinStep{
		{"Device facts and thermal status", func(ctx context.Context) error {
			report := details(r)
			report.Thermal = thermal.Collect(ctx)
			return api.Checkin(ctx, report)
		}},
		{"Hardware and software inventory", func(ctx context.Context) error {
			return api.SendInventory(ctx, inventory.Collect(ctx))
		}},
		{"Configured monitoring checks", func(ctx context.Context) error {
			cfg, err := api.GetCheckConfig(ctx)
			if err != nil || !cfg.Enabled {
				return err
			}
			collector := checks.NewCollector()
			collector.Prime()
			return api.SendCheckReport(ctx, collector.Collect(ctx, cfg))
		}},
		{"Configured Windows event logs", func(ctx context.Context) error {
			if !eventlog.Supported {
				return nil
			}
			cfg, err := api.EventLogConfig(ctx)
			if err != nil || !cfg.Enabled {
				return err
			}
			eventCollectionMu.Lock()
			defer eventCollectionMu.Unlock()
			path := filepath.Join(state.Dir(), "eventlog-cursors.json")
			collector := &eventlog.Collector{Source: eventlog.NewSource(), Send: api.SendEventLogs,
				Cursors: eventlog.LoadCursors(path), Path: path}
			var failures []error
			for _, name := range cfg.Logs {
				if err := collector.SyncLog(ctx, name, cfg.MaxBatch, time.Duration(cfg.InitialLookbackHours)*time.Hour); err != nil {
					failures = append(failures, err)
				}
			}
			return errors.Join(failures...)
		}},
	})
}
