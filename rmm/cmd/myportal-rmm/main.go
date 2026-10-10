// myportal-rmm is MyPortal's RMM agent. It is a separate program from the
// tray app: the tray app installs it, and it runs scripts MyPortal pushes.
//
// Usage:
//
//	myportal-rmm enrol --url https://portal.example.com   (tray token in MYPORTAL_TRAY_TOKEN)
//	myportal-rmm install      install and start the background service
//	myportal-rmm uninstall    stop and remove the background service
//	myportal-rmm run          run in the foreground (the service runs this)
//	myportal-rmm version
//
// Enrolment uses the tray device's own token so the agent joins the same
// company and asset as the tray app. The token is read from the
// MYPORTAL_TRAY_TOKEN environment variable or --tray-token-file, never the
// command line, so it does not show up in process listings.
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/kardianos/service"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
	"github.com/bradhawkins85/myportal-rmm/internal/runner"
	"github.com/bradhawkins85/myportal-rmm/internal/state"
)

// Version is set at build time with -ldflags "-X main.Version=...".
var Version = "0.0.0-dev"

const (
	pollWait       = 25 * time.Second
	maxConcurrent  = 4
	checkinEvery   = 15 * time.Minute
	reportAttempts = 5
)

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "enrol", "enroll":
		err = enrol(os.Args[2:])
	case "install":
		err = control("install")
	case "uninstall":
		err = control("uninstall")
	case "run":
		err = runService()
	case "version", "--version":
		fmt.Println(Version)
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		log.Fatalf("myportal-rmm %s: %v", os.Args[1], err)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: myportal-rmm enrol --url URL | install | uninstall | run | version")
}

func details(r *runner.Runner) client.Details {
	hostname, _ := os.Hostname()
	return client.Details{
		Hostname:     hostname,
		OS:           runtime.GOOS,
		OSVersion:    osVersion(),
		Arch:         runtime.GOARCH,
		AgentVersion: Version,
		Shells:       r.AvailableShells(),
	}
}

func newUID() string {
	buf := make([]byte, 16)
	if _, err := rand.Read(buf); err != nil {
		panic(err)
	}
	return hex.EncodeToString(buf)
}

func enrol(args []string) error {
	fs := flag.NewFlagSet("enrol", flag.ContinueOnError)
	portal := fs.String("url", "", "MyPortal address, e.g. https://portal.example.com")
	tokenFile := fs.String("tray-token-file", "", "file holding the tray device's token")
	if err := fs.Parse(args); err != nil {
		return err
	}
	token := strings.TrimSpace(os.Getenv("MYPORTAL_TRAY_TOKEN"))
	if *tokenFile != "" {
		data, err := os.ReadFile(*tokenFile)
		if err != nil {
			return err
		}
		token = strings.TrimSpace(string(data))
	}
	if *portal == "" || token == "" {
		return errors.New("--url and the tray token (MYPORTAL_TRAY_TOKEN or --tray-token-file) are required")
	}
	if !strings.HasPrefix(strings.ToLower(*portal), "https://") && os.Getenv("MYPORTAL_RMM_ALLOW_HTTP") != "1" {
		return errors.New("--url must use https://")
	}
	existing, err := state.Load()
	uid := existing.AgentUID
	if err != nil || uid == "" {
		uid = newUID()
	}
	r := runner.New(os.TempDir())
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	result, err := client.Enrol(ctx, *portal, token, uid, details(r))
	if err != nil {
		return err
	}
	if err := state.Save(state.State{PortalURL: strings.TrimRight(*portal, "/"), AgentUID: result.AgentUID, AuthToken: result.AuthToken}); err != nil {
		return err
	}
	log.Printf("enrolled as %s; state saved to %s", result.AgentUID, state.Path())
	return nil
}

type program struct {
	cancel context.CancelFunc
	done   chan struct{}
}

func (p *program) Start(s service.Service) error {
	ctx, cancel := context.WithCancel(context.Background())
	p.cancel = cancel
	p.done = make(chan struct{})
	go func() {
		defer close(p.done)
		loop(ctx)
	}()
	return nil
}

func (p *program) Stop(s service.Service) error {
	if p.cancel != nil {
		p.cancel()
	}
	if p.done != nil {
		select {
		case <-p.done:
		case <-time.After(20 * time.Second):
		}
	}
	return nil
}

func serviceConfig() *service.Config {
	executable, _ := os.Executable()
	return &service.Config{
		Name:        "MyPortalRMMAgent",
		DisplayName: "MyPortal RMM Agent",
		Description: "Runs scripts pushed from MyPortal.",
		Executable:  executable,
		Arguments:   []string{"run"},
	}
}

func control(action string) error {
	svc, err := service.New(&program{}, serviceConfig())
	if err != nil {
		return err
	}
	if action == "install" {
		if _, err := state.Load(); err != nil {
			return fmt.Errorf("enrol before installing the service: %w", err)
		}
		if err := service.Control(svc, "install"); err != nil && !strings.Contains(err.Error(), "already exists") {
			return err
		}
		return service.Control(svc, "start")
	}
	_ = service.Control(svc, "stop")
	return service.Control(svc, "uninstall")
}

func runService() error {
	prg := &program{}
	svc, err := service.New(prg, serviceConfig())
	if err != nil {
		return err
	}
	if !service.Interactive() {
		return svc.Run()
	}
	if err := prg.Start(svc); err != nil {
		return err
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	<-stop
	return prg.Stop(svc)
}

func sleep(ctx context.Context, d time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}

// loop polls MyPortal for runs until ctx is cancelled.
func loop(ctx context.Context) {
	var st state.State
	for {
		var err error
		st, err = state.Load()
		if err == nil {
			break
		}
		log.Printf("waiting for enrolment: %v", err)
		sleep(ctx, time.Minute)
		if ctx.Err() != nil {
			return
		}
	}
	workDir := filepath.Join(state.Dir(), "work")
	_ = os.MkdirAll(workDir, 0o700)
	r := runner.New(workDir)
	api := client.New(st.PortalURL, st.AuthToken)
	slots := make(chan struct{}, maxConcurrent)
	var wg sync.WaitGroup
	defer wg.Wait()

	backoff := 5 * time.Second
	lastCheckin := time.Time{}
	for ctx.Err() == nil {
		if time.Since(lastCheckin) > checkinEvery {
			if err := api.Checkin(ctx, details(r)); err != nil {
				log.Printf("check-in failed: %v", err)
			} else {
				lastCheckin = time.Now()
			}
		}
		jobs, err := api.Jobs(ctx, pollWait)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			log.Printf("collecting jobs failed: %v", err)
			if errors.Is(err, client.ErrUnauthorized) {
				backoff = 10 * time.Minute
			}
			sleep(ctx, backoff)
			if backoff < 5*time.Minute {
				backoff *= 2
			}
			continue
		}
		backoff = 5 * time.Second
		for _, job := range jobs {
			select {
			case slots <- struct{}{}:
			case <-ctx.Done():
				return
			}
			wg.Add(1)
			go func(job client.Job) {
				defer wg.Done()
				defer func() { <-slots }()
				execute(ctx, api, r, job)
			}(job)
		}
	}
}

func execute(ctx context.Context, api *client.Client, r *runner.Runner, job client.Job) {
	log.Printf("run %d: starting %s", job.ID, job.Path)
	if err := api.Started(ctx, job.ID); err != nil {
		log.Printf("run %d: could not mark as started: %v", job.ID, err)
	}
	result := r.Run(ctx, job)
	// Report even if the service is stopping, so the run is not left open.
	reportCtx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	delay := 2 * time.Second
	for attempt := 1; attempt <= reportAttempts; attempt++ {
		err := api.Report(reportCtx, job.ID, result)
		if err == nil {
			log.Printf("run %d: reported", job.ID)
			return
		}
		log.Printf("run %d: report attempt %d failed: %v", job.ID, attempt, err)
		sleep(reportCtx, delay)
		delay *= 2
	}
}
