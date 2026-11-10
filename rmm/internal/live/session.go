// Package live serves a technician's live tools session: a terminal, task
// manager, service manager, file browser, registry editor (Windows) and
// real-time CPU and memory usage.
//
// MyPortal lists requested sessions in the job poll. For each one the agent
// opens a WebSocket to /ws/rmm/agent/live/{id} with its bearer token; MyPortal
// relays JSON messages between it and the technician's browser. Requests are
// {"id": "...", "op": "...", "args": {...}} and get one response
// {"id": "...", "ok": true, "result": ...} or {"id": "...", "ok": false,
// "error": "..."}. Streams (metrics, terminal output, file downloads) arrive
// as {"event": "...", "data": ...}. The session ends when either side closes
// the socket; terminals, transfers and metrics stop with it.
package live

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"runtime"
	"runtime/debug"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	// MaxFrame is the largest message either side sends. File chunks are
	// sized well inside it.
	MaxFrame       = 1 << 20
	sendQueue      = 64
	writeTimeout   = 30 * time.Second
	pingEvery      = 25 * time.Second
	readTimeout    = 90 * time.Second
	maxConcurrent  = 8
	handshakeLimit = 30 * time.Second
)

// Request is one technician request relayed by MyPortal.
type Request struct {
	ID   string          `json:"id"`
	Op   string          `json:"op"`
	Args json.RawMessage `json:"args"`
}

type outbound struct {
	ID     string      `json:"id,omitempty"`
	OK     *bool       `json:"ok,omitempty"`
	Result interface{} `json:"result,omitempty"`
	Error  string      `json:"error,omitempty"`
	Event  string      `json:"event,omitempty"`
	Data   interface{} `json:"data,omitempty"`
}

// Hello describes what this device offers; the browser asks for it first.
type Hello struct {
	Hostname   string      `json:"hostname"`
	OS         string      `json:"os"`
	Arch       string      `json:"arch"`
	Version    string      `json:"version"`
	Tools      []string    `json:"tools"`
	Shells     []string    `json:"shells"`
	StartTypes []StartType `json:"start_types"`
	Roots      []string    `json:"roots"`
	Hives      []string    `json:"hives,omitempty"`
	Limits     helloLimits `json:"limits"`
}

type helloLimits struct {
	Terminals     int   `json:"terminals"`
	DownloadBytes int64 `json:"download_bytes"`
	UploadBytes   int64 `json:"upload_bytes"`
	ChunkBytes    int   `json:"chunk_bytes"`
}

// StartType is a service start setting this platform supports.
type StartType struct {
	Value string `json:"value"`
	Label string `json:"label"`
}

// Session is one connected live tools session.
type Session struct {
	ID      int64
	version string
	answer  func(nonce string) string

	conn   *websocket.Conn
	send   chan []byte
	ctx    context.Context
	cancel context.CancelFunc
	slots  chan struct{}

	mu        sync.Mutex
	terminals map[string]terminal
	uploads   map[string]*upload
	downloads map[string]*download
	metrics   context.CancelFunc
	procs     *procSampler
	du        *duScan
}

// Dial connects to MyPortal for session id. baseURL is the portal address the
// agent enrolled with; http(s) becomes ws(s).
func Dial(ctx context.Context, baseURL, token string, id int64) (*websocket.Conn, error) {
	target, err := socketURL(baseURL, id)
	if err != nil {
		return nil, err
	}
	dialer := websocket.Dialer{
		Proxy:            http.ProxyFromEnvironment,
		HandshakeTimeout: handshakeLimit,
		ReadBufferSize:   32 << 10,
		WriteBufferSize:  32 << 10,
	}
	header := http.Header{}
	header.Set("Authorization", "Bearer "+token)
	header.Set("User-Agent", "MyPortal-RMM-Agent")
	conn, resp, err := dialer.DialContext(ctx, target, header)
	if resp != nil && resp.Body != nil {
		resp.Body.Close()
	}
	if err != nil {
		if resp != nil {
			return nil, fmt.Errorf("live session %d: HTTP %d: %w", id, resp.StatusCode, err)
		}
		return nil, fmt.Errorf("live session %d: %w", id, err)
	}
	return conn, nil
}

func socketURL(baseURL string, id int64) (string, error) {
	parsed, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return "", err
	}
	switch strings.ToLower(parsed.Scheme) {
	case "https":
		parsed.Scheme = "wss"
	case "http":
		if os.Getenv("MYPORTAL_RMM_ALLOW_HTTP") != "1" {
			return "", errors.New("live sessions need an https:// portal address")
		}
		parsed.Scheme = "ws"
	default:
		return "", fmt.Errorf("unsupported portal address scheme %q", parsed.Scheme)
	}
	parsed.Path = strings.TrimRight(parsed.Path, "/") + fmt.Sprintf("/ws/rmm/agent/live/%d", id)
	parsed.RawQuery = ""
	return parsed.String(), nil
}

// Serve runs the session on conn until either side closes it or ctx ends.
// answer signs MyPortal's challenge nonce with the agent's key; MyPortal
// relays nothing until an agent with a registered key has answered.
func Serve(ctx context.Context, conn *websocket.Conn, id int64, version string, answer func(nonce string) string) {
	ctx, cancel := context.WithCancel(ctx)
	s := &Session{
		ID:        id,
		version:   version,
		answer:    answer,
		conn:      conn,
		send:      make(chan []byte, sendQueue),
		ctx:       ctx,
		cancel:    cancel,
		slots:     make(chan struct{}, maxConcurrent),
		terminals: map[string]terminal{},
		uploads:   map[string]*upload{},
		downloads: map[string]*download{},
		procs:     newProcSampler(),
	}
	conn.SetReadLimit(MaxFrame)
	writerDone := make(chan struct{})
	go func() {
		defer close(writerDone)
		s.writeLoop()
	}()
	s.readLoop()
	cancel()
	s.cleanup()
	<-writerDone
	_ = conn.Close()
}

func (s *Session) writeLoop() {
	ticker := time.NewTicker(pingEvery)
	defer ticker.Stop()
	for {
		select {
		case <-s.ctx.Done():
			_ = s.conn.WriteControl(websocket.CloseMessage,
				websocket.FormatCloseMessage(websocket.CloseNormalClosure, "session ended"),
				time.Now().Add(5*time.Second))
			return
		case data := <-s.send:
			_ = s.conn.SetWriteDeadline(time.Now().Add(writeTimeout))
			if err := s.conn.WriteMessage(websocket.TextMessage, data); err != nil {
				s.cancel()
				return
			}
		case <-ticker.C:
			if err := s.conn.WriteControl(websocket.PingMessage, nil, time.Now().Add(writeTimeout)); err != nil {
				s.cancel()
				return
			}
		}
	}
}

func (s *Session) readLoop() {
	_ = s.conn.SetReadDeadline(time.Now().Add(readTimeout))
	s.conn.SetPongHandler(func(string) error {
		return s.conn.SetReadDeadline(time.Now().Add(readTimeout))
	})
	for s.ctx.Err() == nil {
		kind, data, err := s.conn.ReadMessage()
		if err != nil {
			return
		}
		_ = s.conn.SetReadDeadline(time.Now().Add(readTimeout))
		if kind != websocket.TextMessage {
			continue
		}
		var challenge struct {
			Type  string `json:"type"`
			Nonce string `json:"nonce"`
		}
		if json.Unmarshal(data, &challenge) == nil && challenge.Type == "challenge" {
			s.answerChallenge(challenge.Nonce)
			continue
		}
		var req Request
		if err := json.Unmarshal(data, &req); err != nil || req.Op == "" {
			continue
		}
		if req.Op == "session.close" {
			return
		}
		if ordered[req.Op] {
			// Keystrokes and upload chunks must keep their order.
			s.handle(req)
			continue
		}
		select {
		case s.slots <- struct{}{}:
		case <-s.ctx.Done():
			return
		}
		go func(req Request) {
			defer func() { <-s.slots }()
			s.handle(req)
		}(req)
	}
}

// ordered requests are handled on the read loop, one after another.
var ordered = map[string]bool{
	"terminal.input":  true,
	"terminal.resize": true,
	"fs.upload.chunk": true,
	"fs.download.ack": true,
	"metrics.start":   true,
	"metrics.stop":    true,
}

func (s *Session) handle(req Request) {
	defer func() {
		if p := recover(); p != nil {
			log.Printf("live session %d: %s: internal error: %v\n%s", s.ID, req.Op, p, debug.Stack())
			s.reply(req.ID, nil, fmt.Errorf("the agent hit an internal error: %v", p))
		}
	}()
	handler, ok := handlers[req.Op]
	if !ok {
		s.reply(req.ID, nil, fmt.Errorf("%s is not supported by this agent", req.Op))
		return
	}
	result, err := handler(s, req)
	if errors.Is(err, errNoReply) {
		return
	}
	s.reply(req.ID, result, err)
}

// errNoReply marks requests that answer themselves (or need no answer).
var errNoReply = errors.New("no reply")

type handlerFunc func(*Session, Request) (interface{}, error)

var handlers = map[string]handlerFunc{}

func register(op string, fn handlerFunc) { handlers[op] = fn }

func (s *Session) reply(id string, result interface{}, err error) {
	if id == "" {
		return
	}
	ok := err == nil
	msg := outbound{ID: id, OK: &ok}
	if err != nil {
		msg.Error = err.Error()
	} else {
		msg.Result = result
		if result == nil {
			msg.Result = struct{}{}
		}
	}
	s.write(msg)
}

func (s *Session) event(name string, data interface{}) bool {
	return s.write(outbound{Event: name, Data: data})
}

// write queues a message, waiting while the socket is busy. It returns false
// once the session has ended.
func (s *Session) answerChallenge(nonce string) {
	if s.answer == nil || nonce == "" {
		log.Printf("live session %d: challenge received but this agent has no key to answer it", s.ID)
		return
	}
	data, err := json.Marshal(map[string]string{"type": "auth", "signature": s.answer(nonce)})
	if err != nil {
		return
	}
	select {
	case s.send <- data:
	case <-s.ctx.Done():
	}
}

func (s *Session) write(msg outbound) bool {
	data, err := json.Marshal(msg)
	if err != nil {
		log.Printf("live session %d: encode: %v", s.ID, err)
		return false
	}
	select {
	case s.send <- data:
		return true
	case <-s.ctx.Done():
		return false
	}
}

func decode(req Request, into interface{}) error {
	if len(req.Args) == 0 || string(req.Args) == "null" {
		return nil
	}
	if err := json.Unmarshal(req.Args, into); err != nil {
		return fmt.Errorf("invalid arguments for %s: %v", req.Op, err)
	}
	return nil
}

func (s *Session) cleanup() {
	s.mu.Lock()
	terms := s.terminals
	s.terminals = map[string]terminal{}
	ups := s.uploads
	s.uploads = map[string]*upload{}
	stop := s.metrics
	s.metrics = nil
	s.mu.Unlock()
	if stop != nil {
		stop()
	}
	s.stopDiskScan()
	for _, term := range terms {
		term.Close()
	}
	for _, up := range ups {
		up.abort()
	}
}

func init() {
	register("hello", func(s *Session, req Request) (interface{}, error) {
		hostname, _ := os.Hostname()
		hello := Hello{
			Hostname:   hostname,
			OS:         runtime.GOOS,
			Arch:       runtime.GOARCH,
			Version:    s.version,
			Tools:      []string{"metrics", "terminal", "processes", "services", "files", "diskusage"},
			Shells:     terminalShells(),
			StartTypes: serviceStartTypes,
			Roots:      fileRoots(),
			Limits: helloLimits{
				Terminals:     maxTerminals,
				DownloadBytes: maxDownloadBytes,
				UploadBytes:   maxUploadBytes,
				ChunkBytes:    chunkBytes,
			},
		}
		if registrySupported {
			hello.Tools = append(hello.Tools, "registry")
			hello.Hives = registryHives
		}
		return hello, nil
	})
}
