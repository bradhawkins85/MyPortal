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
	openChatWindowFunc              = openChatWindow
	requestChatTokenFunc            = requestChatTokenForRoom

	notificationActionOnce    sync.Once
	notificationActionBaseURL string
	notificationActionMu      sync.Mutex
	notificationActions       = map[string]notificationAction{}

	announcedChatRoomsMu sync.Mutex
	announcedChatRooms   = map[int]bool{}
)

type notificationAction struct {
	RoomID    int
	ExpiresAt time.Time
}

// handleChatOpen runs when a technician starts a chat.  The chat window and
// the notification wait until the technician has written something: a chat
// started without a first message is announced by the first chat_message
// instead, so the user is never shown an empty conversation.
func handleChatOpen(payload chatOpenPayload) {
	if strings.TrimSpace(payload.Message) == "" {
		logger.Info("handleChatOpen: room_id=%d has no message yet; waiting for the technician's first message", payload.RoomID)
		return
	}
	if !markChatRoomAnnounced(payload.RoomID) {
		return
	}
	title, body := chatNotificationText(payload)
	announceChatRoom(payload.RoomID, title, body)
}

func handleChatMessage(payload chatMessagePayload) {
	// The first technician message in a room opens the chat window and shows
	// a notification.  Later replies only open or focus the chat window: it is
	// the active conversation surface and keeps polling the room while open.
	// Closed rooms are rejected by the popup/token endpoints so stale messages
	// do not create a fresh "Chat from <device>" room.
	if markChatRoomAnnounced(payload.RoomID) {
		title, body := chatMessageNotificationText(payload)
		announceChatRoom(payload.RoomID, title, body)
		return
	}
	if chatURL := chatOpenURL(payload.RoomID); chatURL != "" {
		go openChatWindowFunc(chatURL, gConfig)
	}
}

func announceChatRoom(roomID int, title, body string) {
	chatURL := chatOpenURL(roomID)
	if chatURL == "" {
		// The portal refused a token (for example, the room was closed), so
		// there is no chat to show.
		return
	}
	actionURL := registerChatOpenAction(roomID)
	if actionURL == "" {
		logger.Warn("announceChatRoom: could not register chat notification action for room_id=%d", roomID)
	}
	go openChatWindowFunc(chatURL, gConfig)
	showChatSessionNotificationFunc(title, body, actionURL)
}

// markChatRoomAnnounced records that roomID has been announced to the user and
// reports whether this call was the first to do so.
func markChatRoomAnnounced(roomID int) bool {
	announcedChatRoomsMu.Lock()
	defer announcedChatRoomsMu.Unlock()
	if announcedChatRooms[roomID] {
		return false
	}
	announcedChatRooms[roomID] = true
	return true
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

func chatMessageNotificationText(payload chatMessagePayload) (string, string) {
	sender := strings.TrimSpace(payload.Sender)
	if sender == "" {
		sender = "A technician"
	}
	title := "New MyPortal chat"
	body := sender + " sent you a message"
	if subject := strings.TrimSpace(payload.Subject); subject != "" {
		body += ": " + subject
	}
	if message := summarizeChatMessage(payload.Message); message != "" {
		body += "\n" + message
	}
	return title, body
}

func chatOpenURL(roomID int) string {
	// Room-specific tray launches require a one-time authenticated popup token.
	// If the portal refuses to issue one (for example, because the chat room was
	// closed), return an empty URL so the client does not open /tray/chat without
	// a token and does not fall back to creating a new chat.
	return requestChatTokenFunc(roomID)
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
		return ""
	}

	id, err := randomActionID()
	if err != nil {
		logger.Warn("registerChatOpenAction: random action id failed: %v", err)
		return ""
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

	chatURL := chatOpenURL(action.RoomID)
	if chatURL == "" {
		http.Error(w, "Unable to open chat. Please try again from the MyPortal tray icon.", http.StatusBadGateway)
		return
	}

	go openChatWindowFunc(chatURL, gConfig)
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
