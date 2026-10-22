package main

import (
	"context"
	"log"
	"strings"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
	"github.com/bradhawkins85/myportal-rmm/internal/inventory"
)

const (
	inventoryStartDelay = 45 * time.Second
	inventoryPoll       = 2 * time.Hour
	inventoryRetryWait  = 15 * time.Minute
	// A portal without the inventory API (older MyPortal) is asked again this often.
	inventoryUnsupportedWait = time.Hour
)

// inventoryLoop sends a hardware and software snapshot when the service
// starts, then whenever MyPortal says one is due (daily, or when a
// technician asks for a refresh).
func inventoryLoop(ctx context.Context, api *client.Client) {
	sleep(ctx, inventoryStartDelay)
	sent := false
	lastError := ""
	for ctx.Err() == nil {
		wait := inventoryPoll
		cfg, err := api.InventoryConfig(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			if err.Error() != lastError {
				log.Printf("inventory: could not ask whether a snapshot is due: %v", err)
				lastError = err.Error()
			}
			if strings.Contains(err.Error(), "HTTP 404") {
				sleep(ctx, inventoryUnsupportedWait)
			} else {
				sleep(ctx, inventoryRetryWait)
			}
			continue
		}
		lastError = ""
		if cfg.PollSeconds >= int(inventoryPoll.Seconds()) {
			wait = time.Duration(cfg.PollSeconds) * time.Second
		}
		if cfg.Due || !sent {
			started := time.Now()
			snapshot := inventory.Collect(ctx)
			if ctx.Err() != nil {
				return
			}
			if err := api.SendInventory(ctx, snapshot); err != nil {
				log.Printf("inventory: sending the snapshot failed: %v", err)
				wait = inventoryRetryWait
			} else {
				sent = true
				log.Printf("inventory: sent (%d programs, %d problems, took %s)", len(snapshot.Software),
					len(snapshot.Errors), time.Since(started).Round(time.Second))
			}
		}
		sleep(ctx, wait)
	}
}
