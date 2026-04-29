// Package notify provides helpers for delivering push notifications to
// the UI agent.  In Phase 3/4 we only support desktop tray notifications
// via the IPC channel; OS-native toast notifications (WinRT / UNUserNotifications)
// are wired in Phase 6.
package notify

import (
	"encoding/json"

	"github.com/bradhawkins85/myportal-tray/internal/ipc"
)

// Notification is a server-pushed notification payload.
type Notification struct {
	Title string `json:"title"`
	Body  string `json:"body"`
	Icon  string `json:"icon,omitempty"`
}

// Send dispatches a notification to all connected UI agents via the IPC
// server.
func Send(srv *ipc.Server, n Notification) {
	payload, _ := json.Marshal(n)
	srv.Broadcast(ipc.Message{
		Type:    "show_notification",
		Payload: json.RawMessage(payload),
	})
}
