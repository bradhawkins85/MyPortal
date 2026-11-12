package main

import (
	"context"
	"path/filepath"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"github.com/bradhawkins85/myportal-tray/internal/minidump"
)

const (
	minidumpFirstScanDelay = 2 * time.Minute
	minidumpScanInterval   = time.Hour
)

type minidumpPoster func(context.Context, []minidump.Summary) error

func postMinidumpBatches(ctx context.Context, dumps []minidump.Summary, post minidumpPoster) error {
	for start := 0; start < len(dumps); start += minidump.MaxDumps {
		end := min(start+minidump.MaxDumps, len(dumps))
		if err := post(ctx, dumps[start:end]); err != nil {
			return err
		}
	}
	return nil
}

// runMinidumpScan reports blue screen dumps MyPortal has not seen yet and
// returns how many it sent. Dumps are only remembered as sent once the
// upload succeeds, so a failure is retried on the next scan.
func runMinidumpScan(ctx context.Context, dir, cachePath string, post minidumpPoster) (int, error) {
	pending, commit, err := minidump.Scan(dir, cachePath, time.Now())
	if err != nil || len(pending) == 0 {
		return 0, err
	}
	if err := postMinidumpBatches(ctx, pending, post); err != nil {
		return 0, err
	}
	return len(pending), commit()
}

// minidumpLoop reports blue screen dumps shortly after the service starts
// (a blue screen always ends in a restart) and then hourly.
func (d *daemon) minidumpLoop(ctx context.Context) {
	dir := minidump.Dir()
	if dir == "" {
		return
	}
	cachePath := filepath.Join(stateDir(), "minidumps.json")
	timer := time.NewTimer(minidumpFirstScanDelay)
	defer timer.Stop()
	lastError := ""
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
		}
		scanCtx, cancel := context.WithTimeout(ctx, 5*time.Minute)
		sent, err := runMinidumpScan(scanCtx, dir, cachePath, d.client.PostMinidumps)
		cancel()
		switch {
		case err != nil && ctx.Err() == nil:
			if err.Error() != lastError {
				logger.Warn("Blue screen dump report: %v; will retry", err)
				lastError = err.Error()
			}
		case sent > 0:
			logger.Info("Reported %d blue screen dump(s) to MyPortal", sent)
			lastError = ""
		}
		timer.Reset(minidumpScanInterval)
	}
}
