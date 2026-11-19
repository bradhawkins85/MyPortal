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
	req := d.heartbeatRequest(facts.ConsoleUser)
	if err := d.client.Heartbeat(ctx, req); err != nil {
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

// heartbeatRequest adds this network's public IPv4 and IPv6 addresses to the
// heartbeat. A failed lookup still sends the heartbeat; the portal then falls
// back to the address it observed.
func (d *daemon) heartbeatRequest(user string) api.HeartbeatRequest {
	req := api.NewHeartbeatRequest(user, updater.AgentVersion)
	ctx, cancel := context.WithTimeout(context.Background(), 25*time.Second)
	defer cancel()
	wan, err := d.client.LookupWANAddresses(ctx)
	if err != nil {
		logger.Debug("WAN IP lookup: %v", err)
	}
	req.WANIPv4, req.WANIPv6 = wan.IPv4, wan.IPv6
	req.WANCity, req.WANCountry, req.WANRegionCode = wan.Geo.City, wan.Geo.Country, wan.Geo.RegionCode
	return req
}
