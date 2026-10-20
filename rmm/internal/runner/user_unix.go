//go:build !windows

package runner

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"syscall"
)

// loggedInUser is the account signed in at the device's console.
type loggedInUser struct {
	name     string
	home     string
	uid, gid uint32
	groups   []uint32
}

// lookupConsoleUser finds who is signed in at the console; replaced in tests.
var lookupConsoleUser = consoleUser

func (r *Runner) findUser() (*loggedInUser, error) {
	name, err := lookupConsoleUser()
	if err != nil {
		return nil, err
	}
	account, err := user.Lookup(name)
	if err != nil {
		return nil, err
	}
	uid, err := strconv.ParseUint(account.Uid, 10, 32)
	if err != nil {
		return nil, err
	}
	gid, err := strconv.ParseUint(account.Gid, 10, 32)
	if err != nil {
		return nil, err
	}
	if uid == 0 {
		return nil, ErrNoLoggedInUser
	}
	found := &loggedInUser{name: account.Username, home: account.HomeDir, uid: uint32(uid), gid: uint32(gid)}
	if ids, err := account.GroupIds(); err == nil {
		for _, id := range ids {
			if value, err := strconv.ParseUint(id, 10, 32); err == nil {
				found.groups = append(found.groups, uint32(value))
			}
		}
	}
	return found, nil
}

// consoleUser returns the name of the user at the console: the owner of
// /dev/console on macOS, the active session on seat0 on Linux.
func consoleUser() (string, error) {
	if runtime.GOOS == "darwin" {
		info, err := os.Stat("/dev/console")
		if err != nil {
			return "", err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || stat.Uid == 0 {
			return "", ErrNoLoggedInUser
		}
		account, err := user.LookupId(strconv.FormatUint(uint64(stat.Uid), 10))
		if err != nil {
			return "", err
		}
		return account.Username, nil
	}
	loginctl, err := exec.LookPath("loginctl")
	if err != nil {
		return "", ErrNoLoggedInUser
	}
	session, err := exec.Command(loginctl, "show-seat", "seat0", "--property=ActiveSession", "--value").Output()
	id := strings.TrimSpace(string(session))
	if err != nil || id == "" {
		return "", ErrNoLoggedInUser
	}
	name, err := exec.Command(loginctl, "show-session", id, "--property=Name", "--value").Output()
	if err != nil || strings.TrimSpace(string(name)) == "" {
		return "", ErrNoLoggedInUser
	}
	return strings.TrimSpace(string(name)), nil
}

func (u *loggedInUser) Close() {}

// workDir is a folder the user can reach: the agent's own folder is
// readable by root only, and so can be root's $TMPDIR on macOS.
func (u *loggedInUser) workDir(string) string {
	return "/tmp"
}

// grant hands the run's working folder to the user.
func (u *loggedInUser) grant(dir string) error {
	return filepath.WalkDir(dir, func(path string, _ fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		return os.Lchown(path, int(u.uid), int(u.gid))
	})
}

// environ is the agent's environment with the user's identity, home and
// session bus, so scripts find the user's own settings.
func (u *loggedInUser) environ() []string {
	values := map[string]string{"HOME": u.home, "USER": u.name, "LOGNAME": u.name}
	runtimeDir := fmt.Sprintf("/run/user/%d", u.uid)
	if info, err := os.Stat(runtimeDir); err == nil && info.IsDir() {
		values["XDG_RUNTIME_DIR"] = runtimeDir
		values["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + filepath.Join(runtimeDir, "bus")
	}
	env := make([]string, 0, len(os.Environ())+len(values))
	for _, item := range os.Environ() {
		name, _, _ := strings.Cut(item, "=")
		if _, replaced := values[name]; !replaced {
			env = append(env, item)
		}
	}
	for _, name := range []string{"HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"} {
		if value, ok := values[name]; ok {
			env = append(env, name+"="+value)
		}
	}
	return env
}

func (u *loggedInUser) run(ctx context.Context, spec launch) (*int, error) {
	if u.uid == 0 {
		return nil, errors.New("refusing to run as root")
	}
	return runExec(exec.CommandContext(ctx, spec.name, spec.args...), spec, func(cmd *exec.Cmd) {
		cmd.SysProcAttr.Credential = &syscall.Credential{Uid: u.uid, Gid: u.gid, Groups: u.groups}
	})
}
