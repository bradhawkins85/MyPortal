package live

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestSocketURL(t *testing.T) {
	got, err := socketURL("https://portal.example.com/base/", 42)
	if err != nil || got != "wss://portal.example.com/base/ws/rmm/agent/live/42" {
		t.Fatalf("socketURL = %q, %v", got, err)
	}
	t.Setenv("MYPORTAL_RMM_ALLOW_HTTP", "")
	if _, err := socketURL("http://portal.example.com", 1); err == nil {
		t.Fatal("plain http must be refused")
	}
}

func TestCPUPercent(t *testing.T) {
	if got := cpuPercent(cpuTimes{busy: 10, total: 100}, cpuTimes{busy: 60, total: 200}); got != 50 {
		t.Fatalf("cpuPercent = %v", got)
	}
	if got := cpuPercent(cpuTimes{busy: 10, total: 100}, cpuTimes{busy: 5, total: 100}); got != 0 {
		t.Fatalf("a counter reset must read 0, got %v", got)
	}
}

func TestDescendantsDeepestFirst(t *testing.T) {
	procs := []Process{{PID: 1, PPID: 0}, {PID: 10, PPID: 1}, {PID: 11, PPID: 10}, {PID: 12, PPID: 11}, {PID: 20, PPID: 1}}
	got := descendants(procs, 10)
	if len(got) != 2 || got[0] != 12 || got[1] != 11 {
		t.Fatalf("descendants = %v", got)
	}
}

func TestProtectedPID(t *testing.T) {
	if protectedPID(os.Getpid()) == nil || protectedPID(0) == nil {
		t.Fatal("the agent itself and pid 0 must be protected")
	}
}

func TestCleanPath(t *testing.T) {
	if _, err := cleanPath("relative/path"); err == nil {
		t.Fatal("relative paths must be refused")
	}
	if _, err := cleanPath(""); err == nil {
		t.Fatal("empty paths must be refused")
	}
}

func TestDiskUsageTotals(t *testing.T) {
	root := t.TempDir()
	write := func(rel string, size int) {
		path := filepath.Join(root, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, make([]byte, size), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("a/big.bin", 5000)
	write("a/b/small.bin", 100)
	write("c.txt", 10)
	for i := 0; i < duKeepFiles+5; i++ {
		write(filepath.Join("many", "f"+string(rune('a'+i%26))+strings.Repeat("x", i/26)), 1)
	}
	scan := &duScan{id: "t", path: root, root: &duNode{name: root, dir: true}, done: make(chan struct{})}
	sc := &duScanner{scan: scan, ctx: context.Background(), sem: make(chan struct{}, 4)}
	sc.walk(scan.root, root)
	sc.wg.Wait()
	finalize(scan.root)
	want := int64(5000 + 100 + 10 + duKeepFiles + 5)
	if scan.root.size != want {
		t.Fatalf("root size = %d, want %d", scan.root.size, want)
	}
	result, err := scan.children(root, 0)
	if err != nil {
		t.Fatal(err)
	}
	items := result["items"].([]duItem)
	if items[0].Name != "a" || items[0].Size != 5100 || items[0].Files != 2 {
		t.Fatalf("largest item = %+v", items[0])
	}
	many, err := scan.children(filepath.Join(root, "many"), 0)
	if err != nil {
		t.Fatal(err)
	}
	other := many["other_files"].(map[string]int64)
	if other["count"] != 5 || many["files"].(int64) != duKeepFiles+5 {
		t.Fatalf("other files = %v, files = %v", other, many["files"])
	}
	if err := scan.remove(filepath.Join(root, "a", "big.bin")); err != nil {
		t.Fatal(err)
	}
	if scan.root.size != want-5000 {
		t.Fatalf("size after remove = %d", scan.root.size)
	}
	if _, err := scan.find(filepath.Dir(root)); err == nil {
		t.Fatal("paths outside the scan must be refused")
	}
}

// harness connects a real agent session to an in-test "portal".
type harness struct {
	t    *testing.T
	conn *websocket.Conn
	seq  int
}

func newHarness(t *testing.T) *harness {
	t.Helper()
	t.Setenv("MYPORTAL_RMM_ALLOW_HTTP", "1")
	accepted := make(chan *websocket.Conn, 1)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/ws/rmm/agent/live/7" || r.Header.Get("Authorization") != "Bearer token" {
			http.Error(w, "unauthorised", http.StatusUnauthorized)
			return
		}
		conn, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		if err != nil {
			return
		}
		accepted <- conn
	}))
	t.Cleanup(server.Close)
	ctx, cancel := context.WithCancel(context.Background())
	agentConn, err := Dial(ctx, server.URL, "token", 7)
	if err != nil {
		t.Fatal(err)
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		Serve(ctx, agentConn, 7, "test")
	}()
	conn := <-accepted
	t.Cleanup(func() {
		conn.Close()
		cancel()
		<-done
	})
	return &harness{t: t, conn: conn}
}

func (h *harness) send(op string, args interface{}) string {
	h.t.Helper()
	h.seq++
	id := "r" + string(rune('0'+h.seq%10)) + strings.Repeat("x", h.seq/10)
	raw, _ := json.Marshal(args)
	if err := h.conn.WriteJSON(Request{ID: id, Op: op, Args: raw}); err != nil {
		h.t.Fatal(err)
	}
	return id
}

type reply struct {
	ID     string          `json:"id"`
	OK     *bool           `json:"ok"`
	Result json.RawMessage `json:"result"`
	Error  string          `json:"error"`
	Event  string          `json:"event"`
	Data   json.RawMessage `json:"data"`
}

// next reads messages until match accepts one.
func (h *harness) next(match func(reply) bool) reply {
	h.t.Helper()
	_ = h.conn.SetReadDeadline(time.Now().Add(20 * time.Second))
	for {
		var msg reply
		if err := h.conn.ReadJSON(&msg); err != nil {
			h.t.Fatal(err)
		}
		if match(msg) {
			return msg
		}
	}
}

func (h *harness) call(op string, args interface{}, out interface{}) {
	h.t.Helper()
	id := h.send(op, args)
	msg := h.next(func(m reply) bool { return m.ID == id })
	if msg.OK == nil || !*msg.OK {
		h.t.Fatalf("%s failed: %s", op, msg.Error)
	}
	if out != nil {
		if err := json.Unmarshal(msg.Result, out); err != nil {
			h.t.Fatal(err)
		}
	}
}

func (h *harness) fail(op string, args interface{}) string {
	h.t.Helper()
	id := h.send(op, args)
	msg := h.next(func(m reply) bool { return m.ID == id })
	if msg.OK == nil || *msg.OK {
		h.t.Fatalf("%s should have failed", op)
	}
	return msg.Error
}

func TestSessionHelloAndUnknownOp(t *testing.T) {
	h := newHarness(t)
	var hello Hello
	h.call("hello", nil, &hello)
	if hello.OS != runtime.GOOS || hello.Version != "test" || len(hello.Roots) == 0 {
		t.Fatalf("hello = %+v", hello)
	}
	if msg := h.fail("nope.nothing", nil); !strings.Contains(msg, "not supported") {
		t.Fatalf("unknown op error = %q", msg)
	}
}

func TestSessionFilesRoundTrip(t *testing.T) {
	h := newHarness(t)
	dir := t.TempDir()
	content := []byte(strings.Repeat("live tools ", 70000))
	target := filepath.Join(dir, "upload.txt")

	var begin struct {
		Upload string `json:"upload"`
		Chunk  int    `json:"chunk_bytes"`
	}
	h.call("fs.upload.begin", map[string]interface{}{"path": target, "size": len(content)}, &begin)
	for seq, offset := 0, 0; offset < len(content); seq++ {
		end := min(offset+begin.Chunk, len(content))
		h.call("fs.upload.chunk", map[string]interface{}{
			"upload": begin.Upload, "seq": seq, "data": base64.StdEncoding.EncodeToString(content[offset:end]),
		}, nil)
		offset = end
	}
	digest := sha256.Sum256(content)
	h.call("fs.upload.end", map[string]interface{}{"upload": begin.Upload, "sha256": hex.EncodeToString(digest[:])}, nil)
	if got, _ := os.ReadFile(target); string(got) != string(content) {
		t.Fatal("uploaded file differs")
	}
	if msg := h.fail("fs.upload.begin", map[string]interface{}{"path": target, "size": 1}); !strings.Contains(msg, "exists") {
		t.Fatalf("overwrite without consent: %q", msg)
	}

	var listing struct {
		Entries []Entry `json:"entries"`
	}
	h.call("fs.list", map[string]string{"path": dir}, &listing)
	if len(listing.Entries) != 1 || listing.Entries[0].Name != "upload.txt" {
		t.Fatalf("listing = %+v", listing.Entries)
	}

	var started struct {
		Transfer string `json:"transfer"`
		Size     int64  `json:"size"`
	}
	h.call("fs.download", map[string]string{"path": target}, &started)
	var received []byte
	for {
		msg := h.next(func(m reply) bool { return m.Event == "fs.chunk" })
		var chunk struct {
			Data   string `json:"data"`
			EOF    bool   `json:"eof"`
			SHA256 string `json:"sha256"`
			Error  string `json:"error"`
		}
		_ = json.Unmarshal(msg.Data, &chunk)
		if chunk.Error != "" {
			t.Fatal(chunk.Error)
		}
		data, _ := base64.StdEncoding.DecodeString(chunk.Data)
		received = append(received, data...)
		_ = h.conn.WriteJSON(Request{Op: "fs.download.ack", Args: json.RawMessage(`{"transfer":"` + started.Transfer + `"}`)})
		if chunk.EOF {
			if chunk.SHA256 != hex.EncodeToString(digest[:]) {
				t.Fatal("download checksum mismatch")
			}
			break
		}
	}
	if string(received) != string(content) {
		t.Fatal("downloaded file differs")
	}

	h.call("fs.rename", map[string]string{"path": target, "name": "renamed.txt"}, nil)
	h.call("fs.mkdir", map[string]string{"path": filepath.Join(dir, "sub")}, nil)
	h.call("fs.delete", map[string]string{"path": filepath.Join(dir, "renamed.txt")}, nil)
	if msg := h.fail("fs.delete", map[string]string{"path": string(filepath.Separator)}); !strings.Contains(msg, "root") {
		t.Fatalf("deleting the root: %q", msg)
	}
	if msg := h.fail("fs.rename", map[string]string{"path": filepath.Join(dir, "sub"), "name": "../escape"}); !strings.Contains(msg, "slashes") {
		t.Fatalf("rename with a path: %q", msg)
	}
}

func TestSessionMetricsAndProcesses(t *testing.T) {
	h := newHarness(t)
	h.call("metrics.start", map[string]int{"interval_ms": 500}, nil)
	msg := h.next(func(m reply) bool { return m.Event == "metrics" })
	var metrics Metrics
	if err := json.Unmarshal(msg.Data, &metrics); err != nil || metrics.MemoryTotal == 0 {
		t.Fatalf("metrics = %+v (%v)", metrics, err)
	}
	h.call("metrics.stop", nil, nil)
	var list struct {
		Processes []Process `json:"processes"`
	}
	h.call("process.list", nil, &list)
	found := false
	for _, proc := range list.Processes {
		found = found || proc.PID == os.Getpid()
	}
	if !found {
		t.Fatal("the test process is missing from the process list")
	}
	if msg := h.fail("process.kill", map[string]int{"pid": os.Getpid()}); !strings.Contains(msg, "cannot be ended") {
		t.Fatalf("killing the agent: %q", msg)
	}
}

func TestSessionDiskUsage(t *testing.T) {
	h := newHarness(t)
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "file.bin"), make([]byte, 4096), 0o644); err != nil {
		t.Fatal(err)
	}
	var started struct {
		Scan string `json:"scan"`
	}
	h.call("diskusage.scan", map[string]string{"path": dir}, &started)
	done := h.next(func(m reply) bool { return m.Event == "diskusage.done" })
	var summary struct {
		Size int64 `json:"size"`
	}
	_ = json.Unmarshal(done.Data, &summary)
	if summary.Size != 4096 {
		t.Fatalf("scan size = %d", summary.Size)
	}
	var children struct {
		Items []duItem `json:"items"`
	}
	h.call("diskusage.children", map[string]string{"scan": started.Scan, "path": dir}, &children)
	if len(children.Items) != 1 || children.Items[0].Name != "file.bin" {
		t.Fatalf("children = %+v", children.Items)
	}
}

func TestSessionTerminal(t *testing.T) {
	if runtime.GOOS == "windows" || len(terminalShells()) == 0 {
		t.Skip("needs a Unix shell")
	}
	h := newHarness(t)
	h.call("terminal.open", map[string]interface{}{"term": "t1", "shell": "sh", "cols": 80, "rows": 24}, nil)
	_ = h.conn.WriteJSON(Request{Op: "terminal.input", Args: json.RawMessage(
		`{"term":"t1","data":"` + base64.StdEncoding.EncodeToString([]byte("echo live-$((40+2))\nexit\n")) + `"}`)})
	var output strings.Builder
	h.next(func(m reply) bool {
		if m.Event == "terminal.output" {
			var data struct {
				Data string `json:"data"`
			}
			_ = json.Unmarshal(m.Data, &data)
			raw, _ := base64.StdEncoding.DecodeString(data.Data)
			output.Write(raw)
		}
		return m.Event == "terminal.exit"
	})
	if !strings.Contains(output.String(), "live-42") {
		t.Fatalf("terminal output = %q", output.String())
	}
	if msg := h.fail("terminal.open", map[string]interface{}{"term": "t2", "shell": "definitely-not-a-shell"}); !strings.Contains(msg, "not available") {
		t.Fatalf("unknown shell: %q", msg)
	}
}
