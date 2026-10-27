package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/config"
	"github.com/bradhawkins85/myportal-tray/internal/ipc"
)

func TestServiceChangesDefenderPreferencesOnlyThroughTamperAwarePolicy(t *testing.T) {
	source, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}

	// Preference changes must go through defender.ApplyPolicy, which skips
	// changes Tamper Protection could block instead of attempting them.
	for _, forbidden := range []string{"Add-MpPreference", "Set-MpPreference", "Remove-MpPreference"} {
		if strings.Contains(string(source), forbidden) {
			t.Errorf("tray service must not modify Defender preferences directly: found %q", forbidden)
		}
	}
	if !strings.Contains(string(source), "defender.ApplyPolicy(") {
		t.Error("tray service no longer applies the portal's Defender policy")
	}
}

func TestDeliverUserSessionMessageQueuesAndLaunchesUIWhenNoIPCClient(t *testing.T) {
	d := &daemon{}
	previousDesktop := linuxDesktopInstalled
	linuxDesktopInstalled = func() bool { return true }
	t.Cleanup(func() { linuxDesktopInstalled = previousDesktop })

	previousLaunch := launchTrayUIForActiveUserFunc
	launches := 0
	launchTrayUIForActiveUserFunc = func() error {
		launches++
		return nil
	}
	t.Cleanup(func() { launchTrayUIForActiveUserFunc = previousLaunch })

	msg := ipc.Message{
		Type:    "chat_open",
		Payload: json.RawMessage(`{"room_id":77}`),
	}
	d.deliverUserSessionMessage(msg)

	if launches != 1 {
		t.Fatalf("launches = %d, want 1", launches)
	}
	pending := d.consumePendingUIMessage()
	if pending == nil {
		t.Fatal("expected pending UI message")
	}
	if pending.Type != msg.Type || string(pending.Payload) != string(msg.Payload) {
		t.Fatalf("pending = %#v, want %#v", pending, msg)
	}
	if again := d.consumePendingUIMessage(); again != nil {
		t.Fatalf("pending message was not consumed: %#v", again)
	}
}

func TestProcessDefenderCommandsDoesNotPollCommandsWhenExcluded(t *testing.T) {
	commandPolls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/tray/defender/policy":
			_ = json.NewEncoder(w).Encode(api.DefenderPolicy{Enabled: false})
		case "/api/tray/defender/commands":
			commandPolls++
			_ = json.NewEncoder(w).Encode(map[string]interface{}{"commands": []api.DefenderCommand{}})
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	d := &daemon{client: api.New(server.URL)}
	d.processDefenderCommands()

	if commandPolls != 0 {
		t.Fatalf("command polls = %d, want 0 for an excluded device", commandPolls)
	}
}

func TestProcessDefenderCommandsRechecksPolicyAfterClaim(t *testing.T) {
	policyChecks := 0
	resultUploads := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/tray/defender/policy":
			policyChecks++
			_ = json.NewEncoder(w).Encode(api.DefenderPolicy{Enabled: policyChecks == 1})
		case "/api/tray/defender/commands":
			_ = json.NewEncoder(w).Encode(map[string]interface{}{"commands": []api.DefenderCommand{{
				ID: 17, CommandType: "quick_scan",
			}}})
		case "/api/tray/defender/commands/17/result":
			resultUploads++
			w.WriteHeader(http.StatusOK)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	d := &daemon{client: api.New(server.URL)}
	d.processDefenderCommands()

	if policyChecks != 2 {
		t.Fatalf("policy checks = %d, want 2", policyChecks)
	}
	if resultUploads != 0 {
		t.Fatalf("result uploads = %d, want 0 because the command must not execute", resultUploads)
	}
}

func TestUninstallToleratesMissingService(t *testing.T) {
	source, err := os.ReadFile("main.go")
	if err != nil {
		t.Fatal(err)
	}
	src := string(source)

	// The uninstall path must check service.Status() before calling
	// Uninstall so that a missing service (e.g. after a failed prior
	// install) is treated as a no-op rather than an error.
	if !strings.Contains(src, "service.ErrNotInstalled") {
		t.Error("main.go must check service.ErrNotInstalled before uninstalling")
	}
	if !strings.Contains(src, "handleServiceCommand") {
		t.Error("main.go must define handleServiceCommand for install/uninstall subcommands")
	}
	// The install/uninstall subcommand check must run before config.Load()
	// so a failed prior install that never wrote the registry/env config
	// does not prevent cleanup.
	cmdIdx := strings.Index(src, `"install" || cmd == "uninstall"`)
	cfgIdx := strings.Index(src, "config.Load()")
	if cmdIdx < 0 || cfgIdx < 0 || cmdIdx > cfgIdx {
		t.Error("install/uninstall subcommand check must appear before config.Load() in main()")
	}
}

func TestEnrolmentRetryRetainsIdentityAcrossServiceRestart(t *testing.T) {
	previousStateDir := stateDir
	dir := t.TempDir()
	stateDir = func() string { return dir }
	t.Cleanup(func() { stateDir = previousStateDir })
	var requests []api.EnrolRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var req api.EnrolRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		requests = append(requests, req)
		if len(requests) == 1 {
			// A failed response can follow successful device persistence on the server.
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		_ = json.NewEncoder(w).Encode(api.EnrolResponse{DeviceUID: req.DeviceUID, AuthToken: "device-auth"})
	}))
	defer server.Close()
	cfg := &config.Config{PortalURL: server.URL, EnrolToken: "install-token"}
	if err := newDaemon(cfg).ensureEnrolled(); err == nil {
		t.Fatal("first enrolment should fail")
	}
	pending := loadStateFile(pendingStateFileName)
	if pending == nil || pending.DeviceUID == "" || pending.AuthToken != "" {
		t.Fatalf("pending state = %#v", pending)
	}
	if loadState() != nil {
		t.Fatal("pending identity must not appear as completed enrolment")
	}
	// A fresh daemon must use the same identity and obtain working credentials.
	d := newDaemon(cfg)
	if err := d.ensureEnrolled(); err != nil {
		t.Fatal(err)
	}
	if len(requests) != 2 || requests[0].DeviceUID != requests[1].DeviceUID {
		t.Fatalf("enrolment identities changed: %#v", requests)
	}
	if d.client.DeviceUID() != pending.DeviceUID || loadState().AuthToken != "device-auth" {
		t.Fatal("credentials were not installed and persisted")
	}
}

func TestRetryEnrolmentRecoversAndStops(t *testing.T) {
	stop := make(chan struct{})
	attempts := 0
	ok := retryEnrolment(stop, func() error {
		attempts++
		if attempts == 1 {
			return errors.New("response timed out")
		}
		return nil
	}, func(int) time.Duration { return time.Millisecond })
	if !ok || attempts != 2 {
		t.Fatalf("result=%v attempts=%d", ok, attempts)
	}
	attempts = 0
	ok = retryEnrolment(stop, func() error {
		attempts++
		close(stop)
		return errors.New("offline")
	}, func(int) time.Duration { return time.Hour })
	if ok || attempts != 1 {
		t.Fatalf("result=%v attempts=%d", ok, attempts)
	}
}

func TestHeadlessLinuxDropsUserSessionMessages(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("only Linux installs can be headless")
	}
	d := &daemon{}
	previousDesktop := linuxDesktopInstalled
	linuxDesktopInstalled = func() bool { return false }
	previousLaunch := launchTrayUIForActiveUserFunc
	launches := 0
	launchTrayUIForActiveUserFunc = func() error {
		launches++
		return nil
	}
	t.Cleanup(func() {
		linuxDesktopInstalled = previousDesktop
		launchTrayUIForActiveUserFunc = previousLaunch
	})

	d.deliverUserSessionMessage(ipc.Message{Type: "chat_open"})

	if launches != 0 || d.consumePendingUIMessage() != nil {
		t.Fatalf("a headless install must not queue or launch a UI (launches = %d)", launches)
	}
	if !headless() || uiReadsState("linux") {
		t.Fatal("a Linux install without the UI must be headless and keep its state private")
	}
	linuxDesktopInstalled = func() bool { return true }
	if headless() || !uiReadsState("linux") {
		t.Fatal("a Linux desktop install must share its state with the UI")
	}
}

func TestWolMagicPacketIsStandard102ByteFormat(t *testing.T) {
	mac, err := net.ParseMAC("AA:BB:CC:DD:EE:FF")
	if err != nil {
		t.Fatalf("parse MAC: %v", err)
	}
	pkt := wolMagicPacket(mac)
	if len(pkt) != 102 {
		t.Fatalf("magic packet is %d bytes, want 102", len(pkt))
	}
	for i := 0; i < 6; i++ {
		if pkt[i] != 0xFF {
			t.Fatalf("byte %d is 0x%02x, want 0xFF", i, pkt[i])
		}
	}
}

func TestWolMagicPacketRepeatsMACSixteenTimes(t *testing.T) {
	mac, err := net.ParseMAC("0A:1B:2C:3D:4E:5F")
	if err != nil {
		t.Fatalf("parse MAC: %v", err)
	}
	pkt := wolMagicPacket(mac)
	for i := 0; i < 16; i++ {
		start := 6 + i*6
		if !bytes.Equal(pkt[start:start+6], []byte(mac)) {
			t.Fatalf("block %d = %v, want %v", i, pkt[start:start+6], mac)
		}
	}
}
