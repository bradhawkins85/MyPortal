// Package logger provides a simple structured logger for the tray app.
//
// Logs are written next to the running executable (in a "logs" subdirectory)
// so administrators can find them without hunting through %ProgramData%.
// If that directory cannot be created or written to (typical for the UI
// agent running as a non-elevated user against C:\Program Files), the
// logger falls back to the platform-standard location:
//   - Windows: %ProgramData%\MyPortal\tray\logs\<name>.log
//   - macOS:   /Library/Logs/MyPortal/tray/<name>.log
//
// Standard output is also used (captured by systemd / launchd / the
// service host).
package logger

import (
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"
)

var (
	mu       sync.Mutex
	writer   io.Writer = os.Stdout
	logger             = log.New(os.Stdout, "", 0)
	debug    bool
	logPath  string
)

// Init opens the platform log file in addition to stdout. Debug logging is
// enabled by default; set MYPORTAL_TRAY_DEBUG=0 (or "false") to suppress
// debug-level lines.
func Init(name string) error {
	enableDebugFromEnv()

	dirs := candidateLogDirs()
	var (
		f       *os.File
		openErr error
		path    string
	)
	for _, dir := range dirs {
		if dir == "" {
			continue
		}
		if err := os.MkdirAll(dir, 0750); err != nil {
			openErr = fmt.Errorf("logger: create log dir %q: %w", dir, err)
			continue
		}
		candidate := filepath.Join(dir, name+".log")
		file, err := os.OpenFile(candidate, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0640)
		if err != nil {
			openErr = fmt.Errorf("logger: open %q: %w", candidate, err)
			continue
		}
		f = file
		path = candidate
		openErr = nil
		break
	}
	if f == nil {
		return openErr
	}

	mu.Lock()
	writer = io.MultiWriter(os.Stdout, f)
	logger = log.New(writer, "", 0)
	logPath = path
	mu.Unlock()

	Info("logger: writing to %s (debug=%t)", path, debug)
	return nil
}

// Path returns the resolved log file path (empty before Init succeeds).
func Path() string {
	mu.Lock()
	defer mu.Unlock()
	return logPath
}

// SetDebug enables or disables debug-level output at runtime.
func SetDebug(on bool) {
	mu.Lock()
	debug = on
	mu.Unlock()
}

// DebugEnabled reports whether debug-level logging is on.
func DebugEnabled() bool {
	mu.Lock()
	defer mu.Unlock()
	return debug
}

func enableDebugFromEnv() {
	mu.Lock()
	defer mu.Unlock()
	v := strings.ToLower(strings.TrimSpace(os.Getenv("MYPORTAL_TRAY_DEBUG")))
	switch v {
	case "0", "false", "no", "off":
		debug = false
	default:
		// Default-on: any other value (including empty) enables debug
		// logging so first-install diagnostics are captured automatically.
		debug = true
	}
}

// candidateLogDirs returns the preferred log directories in priority order:
//  1. <executable directory>/logs
//  2. Platform-standard fallback (writable by the running user's privilege).
func candidateLogDirs() []string {
	var dirs []string
	if exe, err := os.Executable(); err == nil {
		if resolved, err := filepath.EvalSymlinks(exe); err == nil {
			exe = resolved
		}
		dirs = append(dirs, filepath.Join(filepath.Dir(exe), "logs"))
	}
	dirs = append(dirs, platformFallbackDir())
	return dirs
}

func platformFallbackDir() string {
	switch runtime.GOOS {
	case "windows":
		base := os.Getenv("ProgramData")
		if base == "" {
			base = `C:\ProgramData`
		}
		return filepath.Join(base, "MyPortal", "tray", "logs")
	default:
		return "/Library/Logs/MyPortal/tray"
	}
}

func format(level, msg string, args ...any) string {
	ts := time.Now().UTC().Format("2006-01-02T15:04:05Z")
	if len(args) > 0 {
		msg = fmt.Sprintf(msg, args...)
	}
	return fmt.Sprintf("%s [%s] %s", ts, level, msg)
}

// Debug logs a debug-level message when debug logging is enabled.
func Debug(msg string, args ...any) {
	mu.Lock()
	on := debug
	mu.Unlock()
	if !on {
		return
	}
	mu.Lock()
	defer mu.Unlock()
	logger.Println(format("DEBUG", msg, args...))
}

// Info logs an informational message.
func Info(msg string, args ...any) {
	mu.Lock()
	defer mu.Unlock()
	logger.Println(format("INFO", msg, args...))
}

// Warn logs a warning.
func Warn(msg string, args ...any) {
	mu.Lock()
	defer mu.Unlock()
	logger.Println(format("WARN", msg, args...))
}

// Error logs an error.
func Error(msg string, args ...any) {
	mu.Lock()
	defer mu.Unlock()
	logger.Println(format("ERROR", msg, args...))
}

// Fatal logs a fatal error and exits.
func Fatal(msg string, args ...any) {
	Error(msg, args...)
	os.Exit(1)
}
