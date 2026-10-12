package runner

import (
	"context"
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/bradhawkins85/myportal-rmm/internal/client"
)

func testFile(path string, data []byte) client.ScriptFile {
	m, s := md5.Sum(data), sha256.Sum256(data)
	return client.ScriptFile{SourcePath: "Files/Common/tool.exe", Destination: path,
		MD5: hex.EncodeToString(m[:]), SHA256: hex.EncodeToString(s[:]), Size: int64(len(data))}
}

func TestPrepareFilesDownloadsMissingAndReusesMatchingFiles(t *testing.T) {
	data := []byte("MZ\x00\xffinstaller")
	path := filepath.Join(t.TempDir(), "nested", "tool.exe")
	r := New(t.TempDir())
	count := 0
	r.DownloadFile = func(ctx context.Context, runID int64, index int, out io.Writer) error {
		if runID != 7 || index != 0 {
			t.Fatal("wrong download identity")
		}
		count++
		_, err := out.Write(data)
		return err
	}
	job := client.Job{ID: 7, Files: []client.ScriptFile{testFile(path, data)}}
	message, err := r.prepareFiles(context.Background(), job)
	if err != nil || count != 1 || !strings.Contains(message, "downloaded and verified") {
		t.Fatalf("download: %s %v", message, err)
	}
	actual, _ := os.ReadFile(path)
	if string(actual) != string(data) {
		t.Fatal("binary contents changed")
	}
	message, err = r.prepareFiles(context.Background(), job)
	if err != nil || count != 1 || !strings.Contains(message, "download skipped") {
		t.Fatalf("reuse: %s %v", message, err)
	}
	if err := os.WriteFile(path, []byte("old contents"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err = r.prepareFiles(context.Background(), job)
	if err != nil || count != 2 {
		t.Fatalf("replacement: %v", err)
	}
}

func TestFailedDownloadsPreserveExistingFileAndRemoveTemporaryFiles(t *testing.T) {
	for _, outcome := range []string{"corrupt", "truncated", "oversized", "network", "cancelled"} {
		t.Run(outcome, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "tool.exe")
			original := []byte("previous good contents")
			if err := os.WriteFile(path, original, 0o600); err != nil {
				t.Fatal(err)
			}
			data := []byte("new installer")
			r := New(t.TempDir())
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			r.DownloadFile = func(_ context.Context, _ int64, _ int, out io.Writer) error {
				switch outcome {
				case "corrupt":
					_, err := out.Write([]byte("bad installer"))
					return err
				case "truncated":
					_, err := out.Write(data[:2])
					return err
				case "oversized":
					_, err := out.Write(append(data, 'x'))
					return err
				case "network":
					return errors.New("network failed")
				case "cancelled":
					cancel()
					_, err := out.Write(data)
					return err
				}
				return nil
			}
			_, err := r.prepareFiles(ctx, client.Job{ID: 7, Files: []client.ScriptFile{testFile(path, data)}})
			if err == nil {
				t.Fatal("failed transfer was accepted")
			}
			actual, _ := os.ReadFile(path)
			if string(actual) != string(original) {
				t.Fatal("failed download damaged existing file")
			}
			entries, _ := os.ReadDir(filepath.Dir(path))
			if len(entries) != 1 {
				t.Fatal("temporary download was left behind")
			}
		})
	}
}

func TestScriptRunsOnlyAfterAllFilesAreVerified(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("test uses bash")
	}
	r := New(t.TempDir())
	if _, err := r.Lookup("bash"); err != nil {
		t.Skip("bash unavailable")
	}
	path := filepath.Join(t.TempDir(), "tool.txt")
	marker := filepath.Join(t.TempDir(), "started")
	data := []byte("expected contents")
	job := client.Job{ID: 7, Language: "bash", Script: "touch '" + marker + "'\ncat '" + path + "'", TimeoutSeconds: 10,
		Files: []client.ScriptFile{testFile(path, data)}}
	r.DownloadFile = func(_ context.Context, _ int64, _ int, out io.Writer) error {
		_, err := out.Write([]byte("incorrect payload"))
		return err
	}
	result := r.Run(context.Background(), job)
	if result.Error == "" {
		t.Fatal("corrupt download did not fail run")
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Fatal("script started before verification")
	}
	r.DownloadFile = func(_ context.Context, _ int64, _ int, out io.Writer) error { _, err := out.Write(data); return err }
	result = r.Run(context.Background(), job)
	if result.Error != "" || result.ExitCode == nil || *result.ExitCode != 0 || !strings.Contains(result.Stdout, string(data)) {
		t.Fatalf("verified file not available to script: %+v", result)
	}
}

func TestEmptyFilesAndInvalidManifests(t *testing.T) {
	r := New(t.TempDir())
	file := testFile(filepath.Join(t.TempDir(), "empty"), nil)
	r.DownloadFile = func(context.Context, int64, int, io.Writer) error { return nil }
	if _, err := r.prepareFiles(context.Background(), client.Job{Files: []client.ScriptFile{file}}); err != nil {
		t.Fatal(err)
	}
	file.MD5 = "invalid"
	if _, err := r.prepareFiles(context.Background(), client.Job{Files: []client.ScriptFile{file}}); err == nil {
		t.Fatal("invalid checksum accepted")
	}
	file = testFile("relative.exe", nil)
	if _, err := r.prepareFiles(context.Background(), client.Job{Files: []client.ScriptFile{file}}); err == nil {
		t.Fatal("relative path accepted")
	}
}

func TestSymlinkDestinationIsNotOverwritten(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation requires privileges")
	}
	dir := t.TempDir()
	target := filepath.Join(dir, "target")
	link := filepath.Join(dir, "link")
	if err := os.WriteFile(target, []byte("original"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, link); err != nil {
		t.Fatal(err)
	}
	r := New(t.TempDir())
	r.DownloadFile = func(context.Context, int64, int, io.Writer) error { t.Fatal("symlink triggered download"); return nil }
	if _, err := r.prepareFiles(context.Background(), client.Job{Files: []client.ScriptFile{testFile(link, []byte("new"))}}); err == nil {
		t.Fatal("symlink accepted")
	}
	contents, _ := os.ReadFile(target)
	if string(contents) != "original" {
		t.Fatal("symlink target changed")
	}
}

func TestFilePreparationSharesScriptTimeout(t *testing.T) {
	r := New(t.TempDir())
	r.DownloadFile = func(ctx context.Context, _ int64, _ int, _ io.Writer) error { <-ctx.Done(); return ctx.Err() }
	file := testFile(filepath.Join(t.TempDir(), "tool"), []byte("data"))
	result := r.Run(context.Background(), client.Job{ID: 7, Language: "bash", Script: "echo should-not-run", TimeoutSeconds: 1, Files: []client.ScriptFile{file}})
	if !result.TimedOut || result.Error == "" || strings.Contains(result.Stdout, "should-not-run") {
		t.Fatalf("file timeout not reported: %+v", result)
	}
}
