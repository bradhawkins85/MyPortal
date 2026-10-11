package rmminstall

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-tray/internal/api"
)

type fakeAPI struct {
	body   []byte
	sha    string
	token  string
	goos   string
	goarch string
}

func (f *fakeAPI) BaseURL() string   { return "https://portal.example.com" }
func (f *fakeAPI) AuthToken() string { return f.token }

func (f *fakeAPI) GetRMMAgentPackage(_ context.Context, goos, goarch string) (*api.RMMAgentPackage, error) {
	f.goos, f.goarch = goos, goarch
	return &api.RMMAgentPackage{Version: "1.2.3", Sha256: f.sha, DownloadPath: "/api/rmm/agent/package/download?os=x"}, nil
}

func (f *fakeAPI) DownloadRMMAgent(_ context.Context, _ string, w io.Writer) error {
	_, err := w.Write(f.body)
	return err
}

type call struct {
	args []string
	env  []string
}

func newInstaller(t *testing.T, f *fakeAPI, fail string) (*Installer, *[]call) {
	t.Helper()
	calls := &[]call{}
	return &Installer{
		API: f,
		Dir: t.TempDir(),
		Run: func(_ context.Context, path string, args []string, env []string) ([]byte, error) {
			if _, err := os.Stat(path); err != nil {
				t.Fatalf("ran a binary that is not there: %v", err)
			}
			*calls = append(*calls, call{args: args, env: env})
			if args[0] == fail {
				return []byte("boom\n"), errors.New("exit status 1")
			}
			return nil, nil
		},
	}, calls
}

func digest(body []byte) string {
	sum := sha256.Sum256(body)
	return hex.EncodeToString(sum[:])
}

func TestInstallEnrolsWithTheTrayTokenAndInstallsTheService(t *testing.T) {
	body := []byte("agent")
	f := &fakeAPI{body: body, sha: strings.ToUpper(digest(body)), token: "tray-token"}
	in, calls := newInstaller(t, f, "")
	version, err := in.Install(context.Background())
	if err != nil || version != "1.2.3" {
		t.Fatalf("Install = %q, %v", version, err)
	}
	if f.goos != runtime.GOOS || f.goarch != runtime.GOARCH {
		t.Fatalf("asked for %s/%s", f.goos, f.goarch)
	}
	got, err := os.ReadFile(filepath.Join(in.Dir, binaryName()))
	if err != nil || string(got) != "agent" {
		t.Fatalf("binary = %q, %v", got, err)
	}
	if len(*calls) != 2 || strings.Join((*calls)[0].args, " ") != "enrol --url https://portal.example.com" ||
		(*calls)[1].args[0] != "install" {
		t.Fatalf("calls = %+v", *calls)
	}
	if strings.Join((*calls)[0].env, " ") != "MYPORTAL_TRAY_TOKEN=tray-token" {
		t.Fatalf("enrol env = %v", (*calls)[0].env)
	}
	for _, c := range *calls {
		for _, arg := range c.args {
			if strings.Contains(arg, "tray-token") {
				t.Fatal("token passed on the command line")
			}
		}
	}

	// Reinstalling removes the old service before replacing the binary.
	*calls = nil
	if _, err := in.Install(context.Background()); err != nil {
		t.Fatal(err)
	}
	if (*calls)[0].args[0] != "uninstall" || len(*calls) != 3 {
		t.Fatalf("reinstall calls = %+v", *calls)
	}
}

func TestInstallRejectsATamperedDownload(t *testing.T) {
	f := &fakeAPI{body: []byte("evil"), sha: digest([]byte("agent")), token: "t"}
	in, calls := newInstaller(t, f, "")
	if _, err := in.Install(context.Background()); err == nil || !strings.Contains(err.Error(), "mismatch") {
		t.Fatalf("err = %v", err)
	}
	if len(*calls) != 0 {
		t.Fatal("ran the agent")
	}
	entries, _ := os.ReadDir(in.Dir)
	if len(entries) != 0 {
		t.Fatalf("left files behind: %v", entries)
	}
}

func TestInstallNeedsADigestAndAToken(t *testing.T) {
	in, _ := newInstaller(t, &fakeAPI{body: []byte("a"), token: "t"}, "")
	if _, err := in.Install(context.Background()); err == nil {
		t.Fatal("installed without a digest")
	}
	in, _ = newInstaller(t, &fakeAPI{body: []byte("a"), sha: digest([]byte("a"))}, "")
	if _, err := in.Install(context.Background()); err == nil {
		t.Fatal("installed without a token")
	}
}

func TestInstallReportsTheAgentsOutputWhenEnrolmentFails(t *testing.T) {
	body := []byte("agent")
	in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "enrol")
	_, err := in.Install(context.Background())
	if err == nil || !strings.Contains(err.Error(), "boom") || len(*calls) != 1 {
		t.Fatalf("err = %v, calls = %+v", err, *calls)
	}
}
