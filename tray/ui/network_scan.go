package main

import (
	"github.com/bradhawkins85/myportal-tray/internal/ipc"
	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// requestNetworkScan asks the privileged service to run the same discovery and
// upload workflow used by its scheduled interval scan.
func requestNetworkScan() {
	if gIPCConn == nil {
		logger.Warn("Manual network scan requested but IPC is not connected")
		return
	}
	if err := ipc.SendTo(gIPCConn, ipc.Message{Type: "scan_network"}); err != nil {
		logger.Warn("Manual network scan request failed: %v", err)
		return
	}
	logger.Info("Manual network scan request sent to service")
}
