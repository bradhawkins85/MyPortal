package overseer

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

type fakeController struct {
	states   map[string]State
	starts   []string
	restarts []string
	fail     error
}

func (f *fakeController) Status(_ context.Context, s Service) (State, error) {
	return f.states[s.Key], nil
}

func (f *fakeController) Start(_ context.Context, s Service) error {
	f.starts = append(f.starts, s.Key)
	if f.fail == nil {
		st := f.states[s.Key]
		st.Running = true
		f.states[s.Key] = st
	}
	return f.fail
}

func (f *fakeController) Restart(_ context.Context, s Service) error {
	f.restarts = append(f.restarts, s.Key)
	return f.fail
}

type fakePortal struct {
	status  *api.OverseerStatus
	err     error
	reports []api.OverseerReport
}

func (f *fakePortal) Report(_ context.Context, r api.OverseerReport) (*api.OverseerStatus, error) {
	f.reports = append(f.reports, r)
	return f.status, f.err
}

type clock struct{ now time.Time }

func (c *clock) Now() time.Time          { return c.now }
func (c *clock) advance(d time.Duration) { c.now = c.now.Add(d) }

func age(seconds int64) *int64 { return &seconds }

func running() State { return State{Installed: true, Enabled: true, Running: true} }

func newTestOverseer(ctrl *fakeController, portal *fakePortal) (*Overseer, *clock) {
	c := &clock{now: time.Date(2026, 11, 4, 10, 0, 0, 0, time.UTC)}
	o := New(ctrl, portal, []Service{{Key: ServiceTray, Name: "tray"}, {Key: ServiceRMM, Name: "rmm"}}, "1.0.0")
	o.Now = c.Now
	return o, c
}

func healthy() *api.OverseerStatus {
	return &api.OverseerStatus{
		Tray: api.OverseerAgentStatus{Enrolled: true, LastSeenAgeSeconds: age(20)},
		RMM:  api.OverseerAgentStatus{Enrolled: true, LastSeenAgeSeconds: age(30)},
	}
}

func TestStoppedServiceStartedAfterGrace(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{
		ServiceTray: {Installed: true, Enabled: true},
		ServiceRMM:  running(),
	}}
	o, c := newTestOverseer(ctrl, &fakePortal{status: healthy()})
	ctx := context.Background()

	o.CheckLocal(ctx)
	c.advance(time.Minute)
	o.CheckLocal(ctx)
	if len(ctrl.starts) != 0 {
		t.Fatalf("started inside the grace period: %v", ctrl.starts)
	}
	c.advance(2 * time.Minute)
	o.CheckLocal(ctx)
	if len(ctrl.starts) != 1 || ctrl.starts[0] != ServiceTray {
		t.Fatalf("starts = %v, want [tray]", ctrl.starts)
	}
	pending := o.PendingActions()
	if len(pending) != 1 || pending[0].Action != ActionStart || !pending[0].Succeeded {
		t.Fatalf("pending = %+v", pending)
	}
}

func TestStoppedServiceIgnoredWhenDisabledOrMissing(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{
		ServiceTray: {Installed: true, Enabled: false},
		ServiceRMM:  {},
	}}
	o, c := newTestOverseer(ctrl, &fakePortal{status: healthy()})
	for i := 0; i < 10; i++ {
		o.CheckLocal(context.Background())
		c.advance(time.Minute)
	}
	if len(ctrl.starts) != 0 {
		t.Fatalf("starts = %v, want none", ctrl.starts)
	}
}

func TestRestartAfterTwoStaleReports(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{ServiceTray: running(), ServiceRMM: running()}}
	status := healthy()
	status.RMM.LastSeenAgeSeconds = age(900)
	portal := &fakePortal{status: status}
	o, c := newTestOverseer(ctrl, portal)
	ctx := context.Background()

	o.CheckLocal(ctx)
	o.CheckRemote(ctx)
	if len(ctrl.restarts) != 0 {
		t.Fatal("restarted on the first stale report")
	}
	c.advance(5 * time.Minute)
	o.CheckRemote(ctx)
	if len(ctrl.restarts) != 1 || ctrl.restarts[0] != ServiceRMM {
		t.Fatalf("restarts = %v, want [rmm]", ctrl.restarts)
	}
	// The recovery is reported on the next check, then cleared.
	c.advance(5 * time.Minute)
	o.CheckRemote(ctx)
	last := portal.reports[len(portal.reports)-1]
	if len(last.Actions) != 1 || last.Actions[0].Service != ServiceRMM || last.Actions[0].Action != ActionRestart {
		t.Fatalf("reported actions = %+v", last.Actions)
	}
	if len(o.PendingActions()) != 0 {
		t.Fatal("pending actions not cleared after a successful report")
	}
	if len(last.Services) != 2 {
		t.Fatalf("reported services = %+v", last.Services)
	}
}

func TestNeverSeenTrayIsRestarted(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{ServiceTray: running(), ServiceRMM: running()}}
	status := healthy()
	status.Tray.LastSeenAgeSeconds = nil
	o, c := newTestOverseer(ctrl, &fakePortal{status: status})
	ctx := context.Background()
	o.CheckLocal(ctx)
	o.CheckRemote(ctx)
	c.advance(6 * time.Minute)
	o.CheckRemote(ctx)
	if len(ctrl.restarts) != 1 || ctrl.restarts[0] != ServiceTray {
		t.Fatalf("restarts = %v, want [tray]", ctrl.restarts)
	}
}

func TestNoRestartWhilePortalUnreachable(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{ServiceTray: running(), ServiceRMM: running()}}
	status := healthy()
	status.RMM.LastSeenAgeSeconds = age(900)
	portal := &fakePortal{status: status}
	o, c := newTestOverseer(ctrl, portal)
	ctx := context.Background()
	o.CheckLocal(ctx)
	o.CheckRemote(ctx) // stale observed
	c.advance(5 * time.Minute)
	portal.err = errors.New("dial tcp: no route to host")
	o.CheckRemote(ctx) // outage resets the observation
	c.advance(5 * time.Minute)
	portal.err = nil
	o.CheckRemote(ctx) // first stale observation again
	if len(ctrl.restarts) != 0 {
		t.Fatalf("restarts = %v, want none", ctrl.restarts)
	}
}

func TestUnenrolledRMMAndStoppedServicesNotRestarted(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{
		ServiceTray: {Installed: true, Enabled: true},
		ServiceRMM:  running(),
	}}
	status := healthy()
	status.Tray.LastSeenAgeSeconds = age(9999)
	status.RMM = api.OverseerAgentStatus{Enrolled: false}
	o, c := newTestOverseer(ctrl, &fakePortal{status: status})
	ctx := context.Background()
	for i := 0; i < 3; i++ {
		o.states = map[string]State{ServiceTray: ctrl.states[ServiceTray], ServiceRMM: running()}
		o.CheckRemote(ctx)
		c.advance(6 * time.Minute)
	}
	if len(ctrl.restarts) != 0 {
		t.Fatalf("restarts = %v, want none", ctrl.restarts)
	}
}

func TestRestartsBackOff(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{ServiceTray: running(), ServiceRMM: running()}}
	status := healthy()
	status.Tray.LastSeenAgeSeconds = age(3600)
	o, c := newTestOverseer(ctrl, &fakePortal{status: status})
	ctx := context.Background()
	o.CheckLocal(ctx)
	// Report every five minutes for an hour while the tray stays disconnected.
	for i := 0; i < 12; i++ {
		o.CheckRemote(ctx)
		c.advance(5 * time.Minute)
	}
	// Restart at 5m, then cooldowns of 10m and 20m allow restarts near 20m and 45m.
	if n := len(ctrl.restarts); n < 2 || n > 3 {
		t.Fatalf("restarts in an hour = %d, want 2-3 with back-off", n)
	}
	// Recovery resets the back-off.
	status.Tray.LastSeenAgeSeconds = age(10)
	o.CheckRemote(ctx)
	if got := o.tracks[ServiceTray].cooldown; got != o.RestartCooldown {
		t.Fatalf("cooldown after recovery = %s", got)
	}
}

func TestCheckIntervalFromPortalIsClamped(t *testing.T) {
	ctrl := &fakeController{states: map[string]State{ServiceTray: running(), ServiceRMM: running()}}
	status := healthy()
	portal := &fakePortal{status: status}
	o, _ := newTestOverseer(ctrl, portal)
	status.CheckIntervalSeconds = 5
	if got := o.CheckRemote(context.Background()); got != time.Minute {
		t.Fatalf("interval = %s, want 1m", got)
	}
	status.CheckIntervalSeconds = 99999
	if got := o.CheckRemote(context.Background()); got != time.Hour {
		t.Fatalf("interval = %s, want 1h", got)
	}
	portal.err = ErrNotEnrolled
	if got := o.CheckRemote(context.Background()); got != o.RemoteInterval {
		t.Fatalf("interval = %s, want default", got)
	}
}

func TestPendingActionsCapped(t *testing.T) {
	o, _ := newTestOverseer(&fakeController{}, &fakePortal{})
	for i := 0; i < maxPendingActions+5; i++ {
		o.record(api.OverseerAction{Service: ServiceTray, Action: ActionStart, Succeeded: true})
	}
	if n := len(o.PendingActions()); n != maxPendingActions {
		t.Fatalf("pending = %d", n)
	}
}

func TestParseSystemctlShow(t *testing.T) {
	cases := []struct {
		out  string
		want State
	}{
		{"LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n", State{Installed: true, Enabled: true, Running: true}},
		{"LoadState=loaded\nActiveState=failed\nUnitFileState=enabled\n", State{Installed: true, Enabled: true}},
		{"LoadState=loaded\nActiveState=activating\nUnitFileState=enabled\n", State{Installed: true, Enabled: true, Running: true}},
		{"LoadState=loaded\nActiveState=inactive\nUnitFileState=disabled\n", State{Installed: true}},
		{"LoadState=masked\nActiveState=inactive\nUnitFileState=masked\n", State{Installed: true}},
		{"LoadState=not-found\nActiveState=inactive\nUnitFileState=\n", State{}},
	}
	for _, tc := range cases {
		if got := parseSystemctlShow([]byte(tc.out)); got != tc.want {
			t.Errorf("parseSystemctlShow(%q) = %+v, want %+v", tc.out, got, tc.want)
		}
	}
}

func TestParseLaunchctl(t *testing.T) {
	print := "system/io.myportal.tray.service = {\n\tactive count = 1\n\tpath = /Library/LaunchDaemons/io.myportal.tray.service.plist\n\tstate = running\n\n\tprogram = /Library/MyPortal/Tray/myportal-tray-service\n}\n"
	if !parseLaunchctlState([]byte(print)) {
		t.Error("running job not detected")
	}
	if parseLaunchctlState([]byte("system/x = {\n\tstate = not running\n}\n")) {
		t.Error("stopped job reported running")
	}
	disabled := "disabled services = {\n\t\"com.apple.ftpd\" => disabled\n\t\"MyPortalRMMAgent\" => true\n\t\"io.myportal.tray.service\" => enabled\n}\n"
	if !launchctlDisabled([]byte(disabled), "MyPortalRMMAgent") {
		t.Error("=> true not treated as disabled")
	}
	if launchctlDisabled([]byte(disabled), "io.myportal.tray.service") {
		t.Error("enabled job treated as disabled")
	}
	if launchctlDisabled([]byte(disabled), "io.myportal.overseer") {
		t.Error("unlisted job treated as disabled")
	}
}
