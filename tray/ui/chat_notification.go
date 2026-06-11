package main

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

type chatOpenPayload struct {
	RoomID       int    `json:"room_id"`
	MatrixRoomID string `json:"matrix_room_id"`
	Subject      string `json:"subject"`
	InitiatedBy  string `json:"initiated_by"`
	Message      string `json:"message"`
}

type chatMessagePayload struct {
	RoomID        int    `json:"room_id"`
	MatrixRoomID  string `json:"matrix_room_id"`
	Subject       string `json:"subject"`
	Sender        string `json:"sender"`
	Message       string `json:"message"`
	MessageID     int    `json:"message_id"`
	MatrixEventID string `json:"matrix_event_id"`
	SentAt        string `json:"sent_at"`
}

var (
	showChatSessionNotificationFunc = showChatSessionNotification

	notificationActionOnce    sync.Once
	notificationActionBaseURL string
	notificationActionMu      sync.Mutex
	notificationActions       = map[string]notificationAction{}
)

type notificationAction struct {
	RoomID    int
	ExpiresAt time.Time
}

func handleChatOpen(payload chatOpenPayload) {
	actionURL := registerChatOpenAction(payload.RoomID)
	if actionURL == "" {
		logger.Warn("handleChatOpen: could not register chat notification action for room_id=%d", payload.RoomID)
	}

	title, body := chatNotificationText(payload)
	showChatSessionNotificationFunc(title, body, actionURL)
}

func handleChatMessage(payload chatMessagePayload) {
	actionURL := registerChatOpenAction(payload.RoomID)
	if actionURL == "" {
		logger.Warn("handleChatMessage: could not register chat notification action for room_id=%d", payload.RoomID)
	}

	title, body := chatMessageNotificationText(payload)
	showChatSessionNotificationFunc(title, body, actionURL)
}

func chatMessageNotificationText(payload chatMessagePayload) (string, string) {
	sender := strings.TrimSpace(payload.Sender)
	if sender == "" {
		sender = "A technician"
	}

	subject := strings.TrimSpace(payload.Subject)
	message := summarizeChatMessage(payload.Message)

	title := "New MyPortal chat message"
	body := sender + " replied to your support chat"
	if subject != "" {
		body += ": " + subject
	}
	if message != "" {
		body += "\n" + message
	}
	return title, body
}

func chatNotificationText(payload chatOpenPayload) (string, string) {
	initiator := strings.TrimSpace(payload.InitiatedBy)
	if initiator == "" {
		initiator = "A technician"
	}

	subject := strings.TrimSpace(payload.Subject)
	message := summarizeChatMessage(payload.Message)

	title := "New MyPortal chat"
	body := initiator + " started a support chat"
	if subject != "" {
		body += ": " + subject
	}
	if message != "" {
		body += "\n" + message
	}
	return title, body
}

func summarizeChatMessage(message string) string {
	message = strings.Join(strings.Fields(strings.TrimSpace(message)), " ")
	const maxRunes = 180
	runes := []rune(message)
	if len(runes) <= maxRunes {
		return message
	}
	return string(runes[:maxRunes-1]) + "…"
}

func registerChatOpenAction(roomID int) string {
	startNotificationActionServer()
	if notificationActionBaseURL == "" {
		return buildChatURL(roomID)
	}

	id, err := randomActionID()
	if err != nil {
		logger.Warn("registerChatOpenAction: random action id failed: %v", err)
		return buildChatURL(roomID)
	}
	notificationActionMu.Lock()
	pruneExpiredNotificationActionsLocked(time.Now())
	notificationActions[id] = notificationAction{
		RoomID:    roomID,
		ExpiresAt: time.Now().Add(24 * time.Hour),
	}
	notificationActionMu.Unlock()
	return notificationActionBaseURL + "/open-chat?id=" + id
}

func startNotificationActionServer() {
	notificationActionOnce.Do(func() {
		listener, err := net.Listen("tcp", "127.0.0.1:0")
		if err != nil {
			logger.Warn("notification action listener failed: %v", err)
			return
		}
		notificationActionBaseURL = "http://" + listener.Addr().String()

		mux := http.NewServeMux()
		mux.HandleFunc("/open-chat", handleNotificationOpenChat)
		server := &http.Server{
			Handler:           mux,
			ReadHeaderTimeout: 5 * time.Second,
		}
		go func() {
			if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
				logger.Warn("notification action server stopped: %v", err)
			}
		}()
	})
}

func handleNotificationOpenChat(w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	now := time.Now()

	notificationActionMu.Lock()
	action, ok := notificationActions[id]
	if ok && now.After(action.ExpiresAt) {
		delete(notificationActions, id)
		ok = false
	}
	notificationActionMu.Unlock()

	if !ok {
		http.Error(w, "This chat notification has expired. Open MyPortal Chat from the tray icon.", http.StatusGone)
		return
	}

	chatURL := requestChatTokenForRoom(action.RoomID)
	if chatURL == "" {
		chatURL = buildChatURL(action.RoomID)
	}
	if chatURL == "" {
		http.Error(w, "Unable to open chat. Please try again from the MyPortal tray icon.", http.StatusBadGateway)
		return
	}

	go openChatWindow(chatURL, gConfig)
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = fmt.Fprint(w, "<html><body><p>Opening MyPortal Chat…</p><script>window.close();</script></body></html>")
}

func pruneExpiredNotificationActionsLocked(now time.Time) {
	for id, action := range notificationActions {
		if now.After(action.ExpiresAt) {
			delete(notificationActions, id)
		}
	}
}

func randomActionID() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}
