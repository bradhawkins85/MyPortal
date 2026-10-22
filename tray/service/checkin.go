package main

import (
	"context"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"github.com/bradhawkins85/myportal-tray/internal/updater"
)

// A manual check-in bypasses only this pass's timers. The background loops keep
// their existing schedules and the WebSocket remains available for commands.
func (d *daemon) fullTrayCheckin() {
	if !d.manualCheckinMu.TryLock() {
		return
	}
	defer d.manualCheckinMu.Unlock()
	facts := collectFacts()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	if err := d.client.Heartbeat(ctx, api.HeartbeatRequest{ConsoleUser: facts.ConsoleUser, AgentVersion: updater.AgentVersion}); err != nil {
		logger.Warn("Manual tray check-in: %v", err)
	}
	cancel()
	d.refreshConfigAndNotify()
	d.reportDefenderStatus()
	d.requestDetectionReport()
	select {
	case d.defenderCommands <- struct{}{}:
	default:
	}
}
