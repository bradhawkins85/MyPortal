package main

import (
	"context"
	"errors"
	"os"
	"os/signal"
	"runtime"
	"strings"
	"syscall"

	"github.com/kardianos/service"

	"github.com/bradhawkins85/myportal-tray/internal/api"
	"github.com/bradhawkins85/myportal-tray/internal/config"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
	"github.com/bradhawkins85/myportal-tray/internal/overseer"
	"github.com/bradhawkins85/myportal-tray/internal/updater"
)

// The overseer runs from this executable as its own service
// ("myportal-tray-service overseer"), so it keeps running while the tray
// service is stopped, wedged or being restarted.
const overseerCommand = "overseer"

func overseerServiceConfig() *service.Config {
	name := "MyPortalOverseer"
	if runtime.GOOS == "darwin" {
		// Matches the LaunchDaemon the pkg installs.
		name = "io.myportal.overseer"
	}
	return &service.Config{
		Name:        name,
		DisplayName: "MyPortal Overseer",
		Description: "Keeps the MyPortal tray service and RMM agent connected to MyPortal, restarting them when they lose their connection.",
		Arguments:   []string{overseerCommand},
	}
}

// overseerPortal reports with the tray device's own token. It re-reads the
// enrolment each time so a device enrolled after the overseer started is used.
type overseerPortal struct{}

func (overseerPortal) Report(ctx context.Context, report api.OverseerReport) (*api.OverseerStatus, error) {
	s := loadState()
	if s == nil || s.AuthToken == "" {
		return nil, overseer.ErrNotEnrolled
	}
	portalURL := s.PortalURL
	if portalURL == "" {
		if cfg, err := config.Load(); err == nil {
			portalURL = cfg.PortalURL
		}
	}
	if portalURL == "" {
		return nil, overseer.ErrNotEnrolled
	}
	client := api.New(portalURL)
	client.SetAuth(s.DeviceUID, s.AuthToken)
	return client.ReportOverseer(ctx, report)
}

type overseerProgram struct {
	cancel context.CancelFunc
	done   chan struct{}
}

func (p *overseerProgram) Start(service.Service) error {
	ctx, cancel := context.WithCancel(context.Background())
	p.cancel = cancel
	p.done = make(chan struct{})
	o := overseer.New(overseer.NewController(), overseerPortal{}, overseer.DefaultServices(), updater.AgentVersion)
	go func() {
		defer close(p.done)
		o.Run(ctx)
	}()
	return nil
}

func (p *overseerProgram) Stop(service.Service) error {
	if p.cancel != nil {
		p.cancel()
		<-p.done
	}
	return nil
}

// runOverseer handles "overseer", "overseer install" and "overseer uninstall".
func runOverseer(args []string) {
	if err := logger.Init("overseer"); err != nil {
		logger.Error("logger init: %v", err)
	}
	cfg := overseerServiceConfig()
	prog := &overseerProgram{}
	svc, err := service.New(prog, cfg)
	if err != nil {
		logger.Fatal("Overseer service init: %v", err)
	}

	if len(args) > 0 {
		switch args[0] {
		case "install":
			// Reinstalling over an existing service is not an error.
			if err := service.Control(svc, "install"); err != nil && !strings.Contains(err.Error(), "already exists") {
				logger.Error("overseer install failed: %v", err)
				os.Exit(1)
			}
			if err := configureServiceResilience(cfg.Name); err != nil {
				logger.Warn("Configure overseer recovery: %v", err)
			}
			if err := svc.Start(); err != nil {
				logger.Warn("Start overseer: %v", err)
			}
			logger.Info("overseer install succeeded")
			return
		case "uninstall":
			if _, err := svc.Status(); errors.Is(err, service.ErrNotInstalled) {
				logger.Info("Overseer is not installed — nothing to remove.")
				return
			}
			_ = svc.Stop()
			if err := service.Control(svc, "uninstall"); err != nil {
				logger.Error("overseer uninstall failed: %v", err)
				os.Exit(1)
			}
			if err := removeSafeBootRegistration(cfg.Name); err != nil {
				logger.Warn("Remove overseer Safe Mode registration: %v", err)
			}
			logger.Info("overseer uninstall succeeded")
			return
		default:
			logger.Error("unknown overseer command %q (use install or uninstall)", args[0])
			os.Exit(2)
		}
	}

	if !service.Interactive() {
		// Also repairs the recovery settings of an MSI-installed service.
		if err := configureServiceResilience(cfg.Name); err != nil {
			logger.Warn("Configure overseer recovery: %v", err)
		}
		if err := svc.Run(); err != nil {
			logger.Fatal("Overseer run: %v", err)
		}
		return
	}

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	_ = prog.Start(svc)
	<-stop
	_ = prog.Stop(svc)
}
