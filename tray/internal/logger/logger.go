// Package logger provides a simple structured logger for the tray app.
// On Windows, logs are written to %ProgramData%\MyPortal\tray\logs\service.log
// On macOS, to /Library/Logs/MyPortal/tray/service.log
// Standard output is also used (captured by systemd/launchd).
package logger

import (
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"runtime"
	"sync"
	"time"
)

var (
	mu     sync.Mutex
	writer io.Writer = os.Stdout
	logger           = log.New(os.Stdout, "", 0)
)

// Init opens the platform log file in addition to stdout.
func Init(name string) error {
	dir := logDir()
	if err := os.MkdirAll(dir, 0750); err != nil {
		return fmt.Errorf("logger: create log dir: %w", err)
	}
	path := filepath.Join(dir, name+".log")
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0640)
	if err != nil {
		return fmt.Errorf("logger: open log file: %w", err)
	}
	mu.Lock()
	defer mu.Unlock()
	writer = io.MultiWriter(os.Stdout, f)
	logger = log.New(writer, "", 0)
	return nil
}

func logDir() string {
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
