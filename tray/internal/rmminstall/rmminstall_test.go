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
	body             []byte
	sha              string
	token            string
	goos             string
	goarch           string
	packageErr       error
	downloadErr      error
	packageCalls     int
	requestedVersion string
}

func (f *fakeAPI) BaseURL() string   { return "https://portal.example.com" }
func (f *fakeAPI) AuthToken() string { return f.token }

func (f *fakeAPI) GetRMMAgentPackage(_ context.Context, goos, goarch string, version ...string) (*api.RMMAgentPackage, error) {
	f.goos, f.goarch = goos, goarch
	f.packageCalls++
	if len(version) > 0 {
		f.requestedVersion = version[0]
	}
	if f.packageErr != nil {
		return nil, f.packageErr
	}
	return &api.RMMAgentPackage{Version: "1.2.3", Sha256: f.sha, DownloadPath: "/api/rmm/agent/package/download?os=x"}, nil
}

func (f *fakeAPI) DownloadRMMAgent(_ context.Context, _ string, w io.Writer) error {
	_, err := w.Write(f.body)
	if f.downloadErr != nil {
		return f.downloadErr
	}
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
			if args[0] == "version" {
				return []byte("0.1.0"), nil
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
	if (*calls)[0].args[0] != "uninstall" || len(*calls) != 2 {
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

func TestEnsureVersionSkipsMissingAndMatchingAgents(t *testing.T) {
	in, calls := newInstaller(t, &fakeAPI{token: "t"}, "")
	if changed, err := in.EnsureVersion(context.Background(), "1.2.3"); changed || err != nil || len(*calls) != 0 {
		t.Fatalf("missing agent: changed=%v err=%v calls=%v", changed, err, *calls)
	}
	if err := os.WriteFile(filepath.Join(in.Dir, binaryName()), []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	in.Run = func(_ context.Context, _ string, args []string, _ []string) ([]byte, error) {
		if args[0] != "version" {
			t.Fatalf("matching agent was modified: %v", args)
		}
		return []byte("v1.2.3\n"), nil
	}
	if changed, err := in.EnsureVersion(context.Background(), "1.2.3"); changed || err != nil {
		t.Fatalf("matching agent: changed=%v err=%v", changed, err)
	}
}

func TestEnsureVersionUpgradesOldAgentWithoutReenrolling(t *testing.T) {
	body := []byte("new")
	in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "")
	final := filepath.Join(in.Dir, binaryName())
	if err := os.WriteFile(final, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	if changed, err := in.EnsureVersion(context.Background(), "v1.2.3"); !changed || err != nil {
		t.Fatalf("changed=%v err=%v", changed, err)
	}
	if len(*calls) != 3 || (*calls)[0].args[0] != "version" || (*calls)[1].args[0] != "uninstall" || (*calls)[2].args[0] != "install" {
		t.Fatalf("calls = %+v", *calls)
	}
	got, _ := os.ReadFile(final)
	if string(got) != "new" {
		t.Fatalf("binary = %q", got)
	}
}

func TestInstallVersionRefusesDifferentReleaseBeforeStoppingAgent(t *testing.T) {
	in, calls := newInstaller(t, &fakeAPI{body: []byte("new"), sha: digest([]byte("new")), token: "t"}, "")
	final := filepath.Join(in.Dir, binaryName())
	_ = os.WriteFile(final, []byte("old"), 0o755)
	if _, err := in.InstallVersion(context.Background(), "1.2.4"); err == nil || !strings.Contains(err.Error(), "does not match") {
		t.Fatalf("err = %v", err)
	}
	got, _ := os.ReadFile(final)
	if len(*calls) != 0 || string(got) != "old" {
		t.Fatalf("calls=%v binary=%q", *calls, got)
	}
}

func TestUpgradeRestoresOldAgentWhenNewServiceFails(t *testing.T) {
	body := []byte("new")
	in, _ := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "")
	final := filepath.Join(in.Dir, binaryName())
	_ = os.WriteFile(final, []byte("old"), 0o755)
	restartedOld := false
	in.Run = func(_ context.Context, path string, args []string, _ []string) ([]byte, error) {
		got, err := os.ReadFile(path)
		if err != nil {
			return nil, err
		}
		if args[0] == "install" {
			if string(got) == "new" {
				return nil, errors.New("new service failed")
			}
			restartedOld = true
		}
		return nil, nil
	}
	if _, err := in.InstallVersion(context.Background(), "1.2.3"); err == nil {
		t.Fatal("expected startup failure")
	}
	got, _ := os.ReadFile(final)
	if !restartedOld || string(got) != "old" {
		t.Fatalf("restarted=%v binary=%q", restartedOld, got)
	}
}

func TestGitHubFirstSucceedsWhenPortalWouldReturnHTTP400(t *testing.T) {
	body := []byte("github agent")
	portal := &fakeAPI{token: "tray-token", packageErr: errors.New("rmm agent package: HTTP 400")}
	in, calls := newInstaller(t, portal, "")
	github := &fakeAPI{body: body, sha: digest(body)}
	in.GitHub = github
	version, err := in.InstallVersion(context.Background(), "v1.2.3")
	if err != nil || version != "1.2.3" {
		t.Fatalf("version=%q err=%v", version, err)
	}
	if portal.packageCalls != 0 || github.packageCalls != 1 || github.requestedVersion != "v1.2.3" {
		t.Fatalf("portal calls=%d GitHub=%+v", portal.packageCalls, github)
	}
	if len(*calls) != 2 || (*calls)[0].env[0] != "MYPORTAL_TRAY_TOKEN=tray-token" {
		t.Fatalf("calls=%+v", *calls)
	}
}

func TestGitHubFailuresFallBackToVerifiedPortalPackage(t *testing.T) {
	for _, failure := range []string{"lookup", "partial download", "checksum mismatch", "invalid checksum"} {
		t.Run(failure, func(t *testing.T) {
			body := []byte("portal agent")
			portal := &fakeAPI{body: body, sha: digest(body), token: "t"}
			github := &fakeAPI{body: []byte("partial github"), sha: digest([]byte("github agent"))}
			switch failure {
			case "lookup":
				github.packageErr = errors.New("GitHub unavailable")
			case "partial download":
				github.downloadErr = errors.New("connection reset")
			case "invalid checksum":
				github.sha = strings.Repeat("z", 64)
			}
			in, _ := newInstaller(t, portal, "")
			in.GitHub = github
			if _, err := in.InstallVersion(context.Background(), "1.2.3"); err != nil {
				t.Fatal(err)
			}
			got, err := os.ReadFile(filepath.Join(in.Dir, binaryName()))
			if err != nil || string(got) != string(body) || portal.requestedVersion != "1.2.3" {
				t.Fatalf("binary=%q err=%v portal=%+v", got, err, portal)
			}
			entries, _ := os.ReadDir(in.Dir)
			if len(entries) != 1 {
				t.Fatalf("left temporary downloads: %v", entries)
			}
		})
	}
}

func TestBothSourcesFailWithoutStoppingExistingAgent(t *testing.T) {
	portal := &fakeAPI{body: []byte("tampered"), sha: digest([]byte("agent")), token: "t"}
	in, calls := newInstaller(t, portal, "")
	in.GitHub = &fakeAPI{packageErr: errors.New("GitHub unavailable")}
	final := filepath.Join(in.Dir, binaryName())
	if err := os.WriteFile(final, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	_, err := in.InstallVersion(context.Background(), "1.2.3")
	if err == nil || !strings.Contains(err.Error(), "GitHub unavailable") || !strings.Contains(err.Error(), "mismatch") {
		t.Fatalf("err=%v", err)
	}
	got, _ := os.ReadFile(final)
	if string(got) != "old" || len(*calls) != 0 {
		t.Fatalf("binary=%q calls=%v", got, *calls)
	}
}

func TestCancelledGitHubAttemptDoesNotTryPortal(t *testing.T) {
	portal := &fakeAPI{token: "t"}
	in, calls := newInstaller(t, portal, "")
	in.GitHub = &fakeAPI{packageErr: context.Canceled}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := in.InstallVersion(ctx, "1.2.3"); !errors.Is(err, context.Canceled) {
		t.Fatalf("err=%v", err)
	}
	if portal.packageCalls != 0 || len(*calls) != 0 {
		t.Fatalf("portal calls=%d service calls=%v", portal.packageCalls, *calls)
	}
}

func TestEnsureVersionUsesGitHubWhilePortalPackageFails(t *testing.T) {
	body := []byte("new GitHub agent")
	portal := &fakeAPI{token: "t", packageErr: errors.New("rmm agent package: HTTP 400")}
	in, calls := newInstaller(t, portal, "")
	github := &fakeAPI{body: body, sha: digest(body)}
	in.GitHub = github
	final := filepath.Join(in.Dir, binaryName())
	if err := os.WriteFile(final, []byte("old"), 0o755); err != nil {
		t.Fatal(err)
	}
	changed, err := in.EnsureVersion(context.Background(), "v1.2.3")
	if err != nil || !changed || portal.packageCalls != 0 {
		t.Fatalf("changed=%v err=%v portal calls=%d", changed, err, portal.packageCalls)
	}
	if len(*calls) != 3 || (*calls)[0].args[0] != "version" || (*calls)[1].args[0] != "uninstall" || (*calls)[2].args[0] != "install" {
		t.Fatalf("calls=%+v", *calls)
	}
	got, _ := os.ReadFile(final)
	if string(got) != string(body) {
		t.Fatalf("binary=%q", got)
	}
}

func TestNewEnablesPublicGitHubSource(t *testing.T) {
	in := New(&fakeAPI{})
	source, ok := in.GitHub.(*githubSource)
	if !ok || source.baseURL != githubReleaseURL || source.client == nil {
		t.Fatalf("source=%+v", in.GitHub)
	}
}

func TestRepairReenrolsExistingAgentAndVerifiesConnection(t *testing.T) {
	body := []byte("new")
	in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "tray-token"}, "")
	_ = os.WriteFile(filepath.Join(in.Dir, binaryName()), []byte("old"), 0o755)
	version, err := in.RepairVersion(context.Background(), "v1.2.3")
	if err != nil || version != "1.2.3" {
		t.Fatalf("version=%q err=%v", version, err)
	}
	var args []string
	for _, c := range *calls {
		args = append(args, c.args[0])
	}
	if strings.Join(args, ",") != "uninstall,enrol,install,verify" {
		t.Fatalf("calls=%+v", *calls)
	}
	if (*calls)[1].env[0] != "MYPORTAL_TRAY_TOKEN=tray-token" {
		t.Fatal("missing tray token")
	}
}

func TestRepairMissingAgentAndConnectionFailure(t *testing.T) {
	for _, failure := range []string{"", "verify"} {
		t.Run(failure, func(t *testing.T) {
			body := []byte("new")
			in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, failure)
			_, err := in.RepairVersion(context.Background(), "1.2.3")
			if (err != nil) != (failure != "") {
				t.Fatalf("err=%v", err)
			}
			wantCalls := 4
			if failure != "" {
				wantCalls++
			}
			if len(*calls) != wantCalls || (*calls)[0].args[0] != "uninstall" || (*calls)[1].args[0] != "enrol" || (*calls)[3].args[0] != "verify" {
				t.Fatalf("calls=%+v", *calls)
			}
			if failure != "" {
				if !strings.Contains(err.Error(), "verify the agent connection") {
					t.Fatal(err)
				}
				if _, statErr := os.Stat(filepath.Join(in.Dir, binaryName())); !errors.Is(statErr, os.ErrNotExist) {
					t.Fatalf("failed repair left binary: %v", statErr)
				}
			}
		})
	}
}

func TestRepairRestoresOldBinaryWhenVerificationFails(t *testing.T) {
	body := []byte("new")
	in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "verify")
	final := filepath.Join(in.Dir, binaryName())
	_ = os.WriteFile(final, []byte("old"), 0o755)
	if _, err := in.RepairVersion(context.Background(), "1.2.3"); err == nil {
		t.Fatal("expected verification failure")
	}
	got, _ := os.ReadFile(final)
	if string(got) != "old" || (*calls)[len(*calls)-1].args[0] != "install" {
		t.Fatalf("binary=%q calls=%+v", got, *calls)
	}
}

func TestRepairUsesDownloadedBinaryWhenOldExecutableIsBroken(t *testing.T) {
	body := []byte("new")
	in, _ := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "")
	final := filepath.Join(in.Dir, binaryName())
	_ = os.WriteFile(final, []byte("broken"), 0o755)
	removed := false
	in.Run = func(_ context.Context, path string, args []string, _ []string) ([]byte, error) {
		contents, _ := os.ReadFile(path)
		if args[0] == "uninstall" {
			if string(contents) == "broken" {
				return nil, errors.New("cannot execute old binary")
			}
			removed = true
		}
		return nil, nil
	}
	if _, err := in.RepairVersion(context.Background(), "1.2.3"); err != nil || !removed {
		t.Fatalf("removed=%v err=%v", removed, err)
	}
}

func TestEnsureVersionRestoresPreviouslyEnrolledMissingBinary(t *testing.T) {
	body := []byte("new")
	in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "")
	in.StateFile = filepath.Join(in.Dir, "rmm-state.json")
	_ = os.WriteFile(in.StateFile, []byte("saved enrolment"), 0o600)
	changed, err := in.EnsureVersion(context.Background(), "1.2.3")
	if !changed || err != nil {
		t.Fatalf("changed=%v err=%v", changed, err)
	}
	if len(*calls) != 4 || (*calls)[0].args[0] != "uninstall" || (*calls)[1].args[0] != "enrol" || (*calls)[3].args[0] != "verify" {
		t.Fatalf("calls=%+v", *calls)
	}
}

func TestEnsureVersionRepairsAnUnreadableExecutable(t *testing.T) {
	body := []byte("new")
	in, calls := newInstaller(t, &fakeAPI{body: body, sha: digest(body), token: "t"}, "version")
	_ = os.WriteFile(filepath.Join(in.Dir, binaryName()), []byte("broken"), 0o755)
	if changed, err := in.EnsureVersion(context.Background(), "1.2.3"); !changed || err != nil {
		t.Fatalf("changed=%v err=%v", changed, err)
	}
	if len(*calls) != 5 || (*calls)[2].args[0] != "enrol" || (*calls)[4].args[0] != "verify" {
		t.Fatalf("calls=%+v", *calls)
	}
}
