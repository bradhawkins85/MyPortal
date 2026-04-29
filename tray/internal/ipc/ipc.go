// Package ipc provides the local inter-process communication channel
// between the tray service (privileged) and the per-session tray UI agent.
//
// Protocol: newline-delimited JSON messages sent over a local socket.
// On Windows the socket is emulated via a named pipe
//   \\.\pipe\myportal-tray-<install-id>
// On other platforms a Unix domain socket is used
//   /tmp/myportal-tray.sock (macOS: /var/run/myportal-tray.sock)
//
// The service listens; the UI agent connects.  Only a single UI agent
// per socket is expected at any time (multi-user / RDP is handled by
// session-tagged socket paths — a follow-up for Phase 5).
package ipc

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"runtime"
	"sync"

	"github.com/bradhawkins85/myportal-tray/internal/logger"
)

// Message is a generic IPC envelope.
type Message struct {
	Type    string          `json:"type"`
	Payload json.RawMessage `json:"payload,omitempty"`
}

// Handler is called for each inbound message.
type Handler func(msg Message)

// Server listens on the local socket and dispatches messages.
type Server struct {
	ln       net.Listener
	mu       sync.Mutex
	clients  []net.Conn
	handlers map[string]Handler
}

// SocketPath returns the platform socket path.
func SocketPath() string {
	switch runtime.GOOS {
	case "windows":
		return `\\.\pipe\myportal-tray`
	case "darwin":
		return "/var/run/myportal-tray.sock"
	default:
		return "/tmp/myportal-tray.sock"
	}
}

// NewServer creates and starts an IPC server.
func NewServer() (*Server, error) {
	path := SocketPath()
	ln, err := listenSocket(path)
	if err != nil {
		return nil, fmt.Errorf("ipc: listen %s: %w", path, err)
	}
	s := &Server{
		ln:       ln,
		handlers: make(map[string]Handler),
	}
	go s.acceptLoop()
	return s, nil
}

// On registers a handler for a given message type.
func (s *Server) On(msgType string, h Handler) {
	s.handlers[msgType] = h
}

// Broadcast sends a message to all connected UI agents.
func (s *Server) Broadcast(msg Message) {
	data, err := json.Marshal(msg)
	if err != nil {
		return
	}
	data = append(data, '\n')

	s.mu.Lock()
	active := make([]net.Conn, 0, len(s.clients))
	for _, c := range s.clients {
		if _, err := c.Write(data); err == nil {
			active = append(active, c)
		} else {
			c.Close()
		}
	}
	s.clients = active
	s.mu.Unlock()
}

// Close shuts down the server.
func (s *Server) Close() {
	s.ln.Close()
}

func (s *Server) acceptLoop() {
	for {
		conn, err := s.ln.Accept()
		if err != nil {
			return // server closed
		}
		s.mu.Lock()
		s.clients = append(s.clients, conn)
		s.mu.Unlock()
		go s.readLoop(conn)
	}
}

func (s *Server) readLoop(conn net.Conn) {
	defer func() {
		conn.Close()
		s.mu.Lock()
		for i, c := range s.clients {
			if c == conn {
				s.clients = append(s.clients[:i], s.clients[i+1:]...)
				break
			}
		}
		s.mu.Unlock()
	}()

	scanner := bufio.NewScanner(conn)
	for scanner.Scan() {
		var msg Message
		if err := json.Unmarshal(scanner.Bytes(), &msg); err != nil {
			continue
		}
		if h, ok := s.handlers[msg.Type]; ok {
			go h(msg)
		}
	}
}

// Dial connects to the IPC server from the UI agent side.
func Dial() (net.Conn, error) {
	return dialSocket(SocketPath())
}

// SendTo sends a single message over conn.
func SendTo(conn net.Conn, msg Message) error {
	data, err := json.Marshal(msg)
	if err != nil {
		return err
	}
	data = append(data, '\n')
	_, err = conn.Write(data)
	return err
}

// ReadFrom reads a single message from conn.
func ReadFrom(conn net.Conn) (*Message, error) {
	scanner := bufio.NewScanner(conn)
	if !scanner.Scan() {
		if err := scanner.Err(); err != nil {
			return nil, err
		}
		return nil, fmt.Errorf("ipc: connection closed")
	}
	var msg Message
	if err := json.Unmarshal(scanner.Bytes(), &msg); err != nil {
		return nil, err
	}
	return &msg, nil
}

// -----------------------------------------------------------------
// helpers — platform-specific listen/dial
// -----------------------------------------------------------------

func listenSocket(path string) (net.Listener, error) {
	if runtime.GOOS == "windows" {
		// Named pipes are handled in platform_windows.go; on other platforms
		// this falls through to the unix path.
		return listenPlatform(path)
	}
	// Remove stale socket file.
	removeSocket(path)
	return net.Listen("unix", path)
}

func dialSocket(path string) (net.Conn, error) {
	if runtime.GOOS == "windows" {
		return dialPlatform(path)
	}
	return net.Dial("unix", path)
}

// Warn so the import doesn't go unused.
var _ = logger.Info
