package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/defender"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

var collectDefenderDetections = defender.CollectDetections

func (d *daemon) requestDetectionReport() {
	select {
	case d.defenderDetections <- struct{}{}:
	default:
	}
}

func (d *daemon) defenderDetectionLoop() {
	if runtime.GOOS != "windows" {
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go func() {
		select {
		case <-d.stopCh:
			cancel()
		case <-ctx.Done():
		}
	}()
	go func() {
		for ctx.Err() == nil {
			err := defender.WatchDetections(ctx, d.requestDetectionReport)
			if ctx.Err() != nil {
				return
			}
			logger.Warn("Defender event subscription: %v; retrying in one minute", err)
			// Also recover protection history if the local event log is unavailable.
			d.requestDetectionReport()
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Minute):
			}
		}
	}()
	d.requestDetectionReport()
	var retry <-chan time.Time
	var retryTimer *time.Timer
	defer func() {
		if retryTimer != nil {
			retryTimer.Stop()
		}
	}()
	for {
		fromEvent := false
		select {
		case <-ctx.Done():
			return
		case <-d.defenderDetections:
			fromEvent = true
		case <-retry:
		}
		if retryTimer != nil {
			retryTimer.Stop()
			retry = nil
		}
		if err := d.reportDefenderDetections(ctx); err != nil {
			logger.Warn("Defender detection upload: %v; retrying in one minute", err)
			retryTimer = time.NewTimer(time.Minute)
			retry = retryTimer.C
		} else if fromEvent {
			// Defender's history provider can lag its event log notification.
			// Reconcile once more without turning idle reporting into a poll.
			retryTimer = time.NewTimer(5 * time.Second)
			retry = retryTimer.C
		}
	}
}

// Only acknowledge fingerprints after successful upload, retaining failures for
// retry. Persist at most the latest 100 records so restarts do not replay history.
func (d *daemon) reportDefenderDetections(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	policy, err := d.client.GetDefenderPolicy(ctx)
	if err != nil {
		return err
	}
	if !policy.Enabled {
		return nil
	}
	detections, err := collectDefenderDetections(ctx)
	if err != nil {
		return err
	}
	path := filepath.Join(stateDir(), "defender-detections.json")
	previous := map[string]string{}
	if data, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(data, &previous)
	}
	if previous == nil {
		previous = map[string]string{}
	}
	next := map[string]string{}
	var uploadErr error
	for i, detection := range detections {
		if i >= 100 {
			break
		}
		body, err := json.Marshal(detection)
		if err != nil {
			return err
		}
		hash := sha256.Sum256(body)
		fingerprint := hex.EncodeToString(hash[:])
		if previous[detection.DetectionUID] == fingerprint {
			next[detection.DetectionUID] = fingerprint
			continue
		}
		if err := d.client.ReportDefenderDetection(ctx, detection); err != nil {
			uploadErr = err
			next[detection.DetectionUID] = previous[detection.DetectionUID]
			continue
		}
		next[detection.DetectionUID] = fingerprint
	}
	if err := os.MkdirAll(stateDir(), 0700); err != nil {
		return err
	}
	data, err := json.Marshal(next)
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, data, 0600); err != nil {
		return err
	}
	return uploadErr
}
