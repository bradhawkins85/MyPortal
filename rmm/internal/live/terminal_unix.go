//go:build linux || darwin

package live

import (
	"errors"
	"os"
	"os/exec"
	"sync"
	"syscall"

	"github.com/creack/pty"
)

var unixShells = []string{"bash", "zsh", "sh"}

func terminalShells() []string {
	var out []string
	for _, name := range unixShells {
		if _, err := exec.LookPath(name); err == nil {
			out = append(out, name)
		}
	}
	return out
}

type unixTerminal struct {
	*os.File
	cmd  *exec.Cmd
	once sync.Once
}

func startTerminal(shell string, cols, rows int) (terminal, error) {
	path, err := exec.LookPath(shell)
	if err != nil {
		return nil, err
	}
	args := []string{}
	if shell != "sh" {
		args = append(args, "-l")
	}
	cmd := exec.Command(path, args...)
	home := os.Getenv("HOME")
	if home == "" {
		home = "/"
	}
	cmd.Dir = home
	cmd.Env = append(os.Environ(), "TERM=xterm-256color", "COLORTERM=truecolor", "SHELL="+path)
	file, err := pty.StartWithSize(cmd, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
	if err != nil {
		return nil, err
	}
	return &unixTerminal{File: file, cmd: cmd}, nil
}

func (t *unixTerminal) Resize(cols, rows int) error {
	return pty.Setsize(t.File, &pty.Winsize{Cols: uint16(cols), Rows: uint16(rows)})
}

func (t *unixTerminal) Wait() (int, error) {
	err := t.cmd.Wait()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return exit.ExitCode(), nil
	}
	if err != nil {
		return -1, err
	}
	return 0, nil
}

// Close ends the shell's whole session (pty.Start makes it a session
// leader) and releases the pseudo terminal.
func (t *unixTerminal) Close() {
	t.once.Do(func() {
		if t.cmd.Process != nil {
			_ = syscall.Kill(-t.cmd.Process.Pid, syscall.SIGHUP)
			_ = t.cmd.Process.Kill()
		}
		_ = t.File.Close()
	})
}
