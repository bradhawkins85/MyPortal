package main

import (
	"context"
	"encoding/json"
	"log"
	"runtime"
	"time"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
	"github.com/bradhawkins85/myportal-rmm/internal/runner"
)

// Runs on startup and every day even without app jobs. Failed reconciliations
// retry sooner; the shared Windows mutex prevents racing an app installation.
func chocolateySourcesLoop(ctx context.Context, api *client.Client, r *runner.Runner) {
	if runtime.GOOS != "windows" {
		return
	}
	sleep(ctx, 45*time.Second)
	for ctx.Err() == nil {
		wait := 15 * time.Minute
		cfg, err := api.ChocolateySources(ctx)
		if err == nil && cfg.Script != "" && json.Valid(cfg.Sources) {
			result := r.Run(ctx, client.Job{Language: "powershell", Script: cfg.Script,
				TimeoutSeconds: 300, Env: map[string]string{"MYPORTAL_CHOCO_SOURCES": string(cfg.Sources)}})
			if result.ExitCode != nil && *result.ExitCode == 0 && result.Error == "" && !result.TimedOut {
				wait = 24 * time.Hour
				log.Print("Chocolatey sources reconciled")
			} else {
				log.Print("Chocolatey source reconciliation failed; retrying in 15 minutes")
			}
		} else {
			log.Print("Chocolatey source settings unavailable; retrying in 15 minutes")
		}
		// Result streams can include sensitive diagnostics; never log them.
		sleep(ctx, wait)
	}
}

// Refresh policy immediately before an app job, so a queued job never restores
// stale sources or credentials after an administrator changes company settings.
func refreshChocolateyPlan(ctx context.Context, api *client.Client, job *client.Job) bool {
	raw, ok := job.Env["MYPORTAL_PATCH_PLAN"]
	if !ok {
		return true
	}
	var plan map[string]json.RawMessage
	if json.Unmarshal([]byte(raw), &plan) != nil {
		return false
	}
	var apps []json.RawMessage
	if json.Unmarshal(plan["apps"], &apps) != nil {
		return false
	}
	if len(apps) == 0 {
		return true
	}
	cfg, err := api.ChocolateySources(ctx)
	if err != nil || !json.Valid(cfg.Sources) {
		return false
	}
	plan["sources"] = cfg.Sources
	updated, err := json.Marshal(plan)
	if err != nil {
		return false
	}
	job.Env["MYPORTAL_PATCH_PLAN"] = string(updated)
	return true
}
