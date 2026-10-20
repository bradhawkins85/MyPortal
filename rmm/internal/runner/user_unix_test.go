//go:build !windows

package runner

import (
	"context"
	"os"
	"os/user"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func withConsoleUser(t *testing.T, lookup func() (string, error)) {
	t.Helper()
	previous := lookupConsoleUser
	lookupConsoleUser = lookup
	t.Cleanup(func() { lookupConsoleUser = previous })
}

func TestRunAsUserWithNobodyLoggedInDoesNotRun(t *testing.T) {
	requireBash(t)
	withConsoleUser(t, func() (string, error) { return "", ErrNoLoggedInUser })
	marker := t.TempDir() + "/ran"
	script := "touch " + marker
	job := client.Job{ID: 21, Language: "bash", Script: script, SHA256: checksum(script), TimeoutSeconds: 30, RunAs: RunAsUser}
	result := New(t.TempDir()).Run(context.Background(), job)
	if result.ExitCode != nil || !strings.Contains(result.Error, "No user is logged in") {
		t.Fatalf("result = %#v", result)
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("the script ran although nobody is logged in")
	}
}

func TestRunAsUserRefusesRoot(t *testing.T) {
	requireBash(t)
	withConsoleUser(t, func() (string, error) { return "root", nil })
	script := "id -u"
	job := client.Job{ID: 22, Language: "bash", Script: script, SHA256: checksum(script), TimeoutSeconds: 30, RunAs: RunAsUser}
	result := New(t.TempDir()).Run(context.Background(), job)
	if result.ExitCode != nil || !strings.Contains(result.Error, "No user is logged in") {
		t.Fatalf("result = %#v", result)
	}
}

func TestRunAsUserRunsWithTheUsersIdentity(t *testing.T) {
	requireBash(t)
	if os.Geteuid() != 0 {
		t.Skip("switching users needs root")
	}
	account, err := user.Lookup("nobody")
	if err != nil {
		t.Skip("no nobody account")
	}
	withConsoleUser(t, func() (string, error) { return "nobody", nil })
	script := strings.Join([]string{
		`echo "uid=$(id -u) user=$USER home=$HOME site=$SITE_CODE"`,
		`echo '{"asset":{"Who":"'"$(id -un)"'"}}' > "$MYPORTAL_RESULT_FILE"`,
	}, "\n")
	job := client.Job{
		ID: 23, Language: "bash", Script: script, SHA256: checksum(script), TimeoutSeconds: 30, RunAs: RunAsUser,
		Env: map[string]string{"SITE_CODE": "SYD"},
	}
	result := New(t.TempDir()).Run(context.Background(), job)
	if result.ExitCode == nil || *result.ExitCode != 0 {
		t.Fatalf("result = %#v", result)
	}
	want := "uid=" + account.Uid + " user=nobody home=" + account.HomeDir + " site=SYD"
	if strings.TrimSpace(result.Stdout) != want {
		t.Fatalf("stdout = %q, want %q", result.Stdout, want)
	}
	if len(result.CustomValues) != 1 || result.CustomValues[0].Value != "nobody" {
		t.Fatalf("custom values = %#v", result.CustomValues)
	}
}

func TestRunAsSystemIsTheDefault(t *testing.T) {
	requireBash(t)
	withConsoleUser(t, func() (string, error) {
		t.Fatal("a system run looked up the logged-in user")
		return "", nil
	})
	script := "echo ok"
	job := client.Job{ID: 24, Language: "bash", Script: script, SHA256: checksum(script), TimeoutSeconds: 30}
	result := New(t.TempDir()).Run(context.Background(), job)
	if result.ExitCode == nil || *result.ExitCode != 0 || strings.TrimSpace(result.Stdout) != "ok" {
		t.Fatalf("result = %#v", result)
	}
}
