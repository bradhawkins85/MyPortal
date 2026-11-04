// Package overseer keeps the tray and RMM agent services connected to MyPortal.
//
// The overseer runs as its own service (MyPortalOverseer) next to the tray
// service and the RMM agent. It does two things:
//
//   - Every minute it checks each agent's service locally. A service that is
//     installed and enabled but has stayed stopped for StoppedGrace is started.
//   - Every few minutes it reports to MyPortal with the tray device's token and
//     reads back how long ago MyPortal last heard from the tray WebSocket and
//     the RMM agent. A running service MyPortal has not heard from for
//     StaleAfter, on two checks at least DisconnectedGrace apart, is restarted.
//
// Nothing is restarted while MyPortal cannot be reached: a restart cannot fix
// a network or server outage. Repeated recoveries of one service back off from
// RestartCooldown to MaxCooldown. Every recovery is reported to MyPortal, which
// records it in the audit log.
package overseer

import (
	"context"
	"errors"
	"fmt"
	"math/rand"
	"sync"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// Service keys used in reports.
const (
	ServiceTray = "tray"
	ServiceRMM  = "rmm"
)

// Recovery actions.
const (
	ActionStart   = "start"
	ActionRestart = "restart"
)

// maxPendingActions caps the actions kept while MyPortal is unreachable.
const maxPendingActions = 20

// ErrNotEnrolled means the tray has no device token yet, so MyPortal cannot
// be asked about the agents' connections.
var ErrNotEnrolled = errors.New("the tray is not enrolled yet")

// Service is one agent service the overseer looks after.
type Service struct {
	// Key is ServiceTray or ServiceRMM.
	Key string
	// Name is the Windows service name, systemd unit or launchd label.
	Name string
	// Plist is the launchd job file (macOS only).
	Plist string
}

// State is what the service manager reports for a service.
type State struct {
	Installed bool
	Enabled   bool
	Running   bool
}

// Controller queries and controls services through the OS service manager.
type Controller interface {
	Status(ctx context.Context, s Service) (State, error)
	Start(ctx context.Context, s Service) error
	Restart(ctx context.Context, s Service) error
}

// Portal sends a report to MyPortal and returns its view of the agents.
type Portal interface {
	Report(ctx context.Context, report api.OverseerReport) (*api.OverseerStatus, error)
}

// Overseer holds the checks' settings and what it has observed so far.
type Overseer struct {
	Controller Controller
	Portal     Portal
	Services   []Service
	Version    string

	LocalInterval     time.Duration
	RemoteInterval    time.Duration
	StoppedGrace      time.Duration
	StaleAfter        time.Duration
	DisconnectedGrace time.Duration
	RestartCooldown   time.Duration
	MaxCooldown       time.Duration

	Now    func() time.Time
	Jitter func(max time.Duration) time.Duration

	mu      sync.Mutex
	tracks  map[string]*track
	pending []api.OverseerAction
	states  map[string]State
}

type track struct {
	stoppedSince time.Time
	staleSince   time.Time
	lastAction   time.Time
	cooldown     time.Duration
}

// New returns an overseer with the default timings.
func New(controller Controller, portal Portal, services []Service, version string) *Overseer {
	return &Overseer{
		Controller:        controller,
		Portal:            portal,
		Services:          services,
		Version:           version,
		LocalInterval:     time.Minute,
		RemoteInterval:    5 * time.Minute,
		StoppedGrace:      3 * time.Minute,
		StaleAfter:        5 * time.Minute,
		DisconnectedGrace: 5 * time.Minute,
		RestartCooldown:   10 * time.Minute,
		MaxCooldown:       2 * time.Hour,
		Now:               time.Now,
		Jitter: func(max time.Duration) time.Duration {
			if max <= 0 {
				return 0
			}
			return time.Duration(rand.Int63n(int64(max)))
		},
	}
}

// Run checks the services until ctx is cancelled.
func (o *Overseer) Run(ctx context.Context) {
	logger.Info("Overseer %s watching %d services", o.Version, len(o.Services))
	// Spread the first report across the fleet: devices often start together.
	nextRemote := o.Now().Add(time.Minute + o.Jitter(o.RemoteInterval/2))
	ticker := time.NewTicker(o.LocalInterval)
	defer ticker.Stop()
	for {
		o.CheckLocal(ctx)
		if !o.Now().Before(nextRemote) {
			interval := o.CheckRemote(ctx)
			nextRemote = o.Now().Add(interval + o.Jitter(interval/5))
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func (o *Overseer) trackFor(key string) *track {
	if o.tracks == nil {
		o.tracks = map[string]*track{}
	}
	t := o.tracks[key]
	if t == nil {
		t = &track{cooldown: o.RestartCooldown}
		o.tracks[key] = t
	}
	return t
}

func (o *Overseer) canAct(t *track, now time.Time) bool {
	return t.lastAction.IsZero() || now.Sub(t.lastAction) >= t.cooldown
}

// acted records an attempt and backs off further attempts on this service.
func (o *Overseer) acted(t *track, now time.Time) {
	if !t.lastAction.IsZero() {
		t.cooldown *= 2
		if t.cooldown > o.MaxCooldown {
			t.cooldown = o.MaxCooldown
		}
	}
	t.lastAction = now
}

func (o *Overseer) record(action api.OverseerAction) {
	if action.Succeeded {
		logger.Info("Overseer: %s %s service (%s)", action.Action, action.Service, action.Reason)
	} else {
		logger.Warn("Overseer: %s %s service failed (%s): %s", action.Action, action.Service, action.Reason, action.Error)
	}
	o.pending = append(o.pending, action)
	if len(o.pending) > maxPendingActions {
		o.pending = o.pending[len(o.pending)-maxPendingActions:]
	}
}

func (o *Overseer) do(ctx context.Context, s Service, action, reason string) {
	ctx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	var err error
	if action == ActionStart {
		err = o.Controller.Start(ctx, s)
	} else {
		err = o.Controller.Restart(ctx, s)
	}
	entry := api.OverseerAction{
		Service: s.Key, Action: action, Reason: reason, OccurredAt: o.Now().UTC(), Succeeded: err == nil,
	}
	if err != nil {
		entry.Error = truncate(err.Error(), 500)
	}
	o.record(entry)
}

// CheckLocal starts services that have stayed stopped for StoppedGrace. The
// grace period lets installers and upgrades stop and replace a service.
func (o *Overseer) CheckLocal(ctx context.Context) map[string]State {
	o.mu.Lock()
	defer o.mu.Unlock()
	states := make(map[string]State, len(o.Services))
	for _, s := range o.Services {
		t := o.trackFor(s.Key)
		st, err := o.Controller.Status(ctx, s)
		if err != nil {
			logger.Warn("Overseer: query %s service: %v", s.Key, err)
			t.stoppedSince = time.Time{}
			continue
		}
		states[s.Key] = st
		now := o.Now()
		if !st.Installed || !st.Enabled || st.Running {
			t.stoppedSince = time.Time{}
			continue
		}
		if t.stoppedSince.IsZero() {
			t.stoppedSince = now
			continue
		}
		if now.Sub(t.stoppedSince) < o.StoppedGrace || !o.canAct(t, now) {
			continue
		}
		o.acted(t, now)
		o.do(ctx, s, ActionStart, fmt.Sprintf("the service was stopped for %s", roundMinutes(now.Sub(t.stoppedSince))))
		t.stoppedSince = time.Time{}
		t.staleSince = time.Time{}
		if after, err := o.Controller.Status(ctx, s); err == nil {
			states[s.Key] = after
		}
	}
	o.states = states
	return states
}

// CheckRemote reports to MyPortal and restarts running services MyPortal has
// stopped hearing from. It returns the interval until the next report.
func (o *Overseer) CheckRemote(ctx context.Context) time.Duration {
	o.mu.Lock()
	defer o.mu.Unlock()
	report := api.OverseerReport{OverseerVersion: o.Version, Actions: append([]api.OverseerAction(nil), o.pending...)}
	for _, s := range o.Services {
		if st, ok := o.states[s.Key]; ok {
			report.Services = append(report.Services, api.OverseerServiceState{
				Service: s.Key, Installed: st.Installed, Enabled: st.Enabled, Running: st.Running,
			})
		}
	}
	reqCtx, cancel := context.WithTimeout(ctx, 45*time.Second)
	status, err := o.Portal.Report(reqCtx, report)
	cancel()
	if err != nil {
		// Unreachable, not enrolled or refused: a restart would not help, and
		// the stale timers must not carry over an outage.
		if !errors.Is(err, ErrNotEnrolled) {
			logger.Warn("Overseer: report to MyPortal: %v", err)
		}
		for _, t := range o.tracks {
			t.staleSince = time.Time{}
		}
		return o.RemoteInterval
	}
	o.pending = o.pending[:0]

	for _, s := range o.Services {
		switch s.Key {
		case ServiceTray:
			o.evaluate(ctx, s, status.Tray, "the tray's WebSocket")
		case ServiceRMM:
			o.evaluate(ctx, s, status.RMM, "the RMM agent")
		}
	}
	interval := o.RemoteInterval
	if status.CheckIntervalSeconds > 0 {
		interval = time.Duration(status.CheckIntervalSeconds) * time.Second
		if interval < time.Minute {
			interval = time.Minute
		}
		if interval > time.Hour {
			interval = time.Hour
		}
	}
	return interval
}

func (o *Overseer) evaluate(ctx context.Context, s Service, agent api.OverseerAgentStatus, what string) {
	t := o.trackFor(s.Key)
	st, ok := o.states[s.Key]
	if !ok || !st.Installed || !st.Running || !agent.Enrolled {
		// Stopped services are CheckLocal's job; an agent MyPortal does not
		// know about cannot be fixed by a restart.
		t.staleSince = time.Time{}
		return
	}
	now := o.Now()
	stale := agent.LastSeenAgeSeconds == nil || time.Duration(*agent.LastSeenAgeSeconds)*time.Second > o.StaleAfter
	if !stale {
		t.staleSince = time.Time{}
		t.cooldown = o.RestartCooldown
		return
	}
	if t.staleSince.IsZero() {
		t.staleSince = now
		return
	}
	if now.Sub(t.staleSince) < o.DisconnectedGrace || !o.canAct(t, now) {
		return
	}
	reason := "MyPortal has never heard from " + what
	if agent.LastSeenAgeSeconds != nil {
		reason = fmt.Sprintf("MyPortal last heard from %s %s ago", what, roundMinutes(time.Duration(*agent.LastSeenAgeSeconds)*time.Second))
	}
	o.acted(t, now)
	o.do(ctx, s, ActionRestart, reason)
	t.staleSince = time.Time{}
}

// PendingActions returns the recoveries not yet reported to MyPortal.
func (o *Overseer) PendingActions() []api.OverseerAction {
	o.mu.Lock()
	defer o.mu.Unlock()
	return append([]api.OverseerAction(nil), o.pending...)
}

func roundMinutes(d time.Duration) string {
	if d < time.Minute {
		return d.Round(time.Second).String()
	}
	return d.Round(time.Minute).String()
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}
