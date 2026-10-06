package agent

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
	"unicode/utf16"
)

func TestWindowsFilePatternAllowlist(t *testing.T) {
	ok := []string{
		`C:\Windows\Logs\CBS\CBS.log`,
		`c:\windows\logs\dism\dism.log`,
		`C:/Windows/INF/setupapi.dev.log`,
		`C:\Windows\debug\NetSetup.LOG`,
		`C:\Windows\CCM\Logs\*.log`,
		`C:\Users\*\AppData\Local\Temp\Outlook Logging\*.etl.txt`,
		`C:\ProgramData\Microsoft\IntuneManagementExtension\Logs\IntuneManagementExtension.log`,
		`C:\ProgramData\Microsoft\Windows\WER\ReportArchive\*\Report.wer`,
		`C:\Windows\SoftwareDistribution\ReportingEvents.log`,
	}
	for _, p := range ok {
		if _, err := windowsPaths.allowedFilePattern(p, WindowsLogFileRoots); err != nil {
			t.Errorf("%s refused: %v", p, err)
		}
	}
	refused := []string{
		`C:\Windows\System32\config\SAM`,
		`C:\Windows\Logs\..\System32\config\SAM.log`,
		`C:\Windows\Logs`,
		`C:\Windows\Logs\CBS\CBS.log:secret`,
		`C:\*\Logs\CBS.log`,
		`C:\Win*\Logs\CBS.log`,
		`C:\Users\bob\Documents\notes.txt`,
		`C:\Users\bob\AppData\Local\Temp\creds.ps1`,
		`\\server\share\Logs\a.log`,
		`D:\Windows\Logs\a.log`,
		`Windows\Logs\a.log`,
		`C:\Windows\Logs\[ab].log`,
		`C:\Windows\Logs.\CBS.log`,
		`/var/log/system.log`,
	}
	for _, p := range refused {
		if _, err := windowsPaths.allowedFilePattern(p, WindowsLogFileRoots); err == nil {
			t.Errorf("%s was allowed", p)
		}
	}
}

func TestMacOSFilePatternAllowlist(t *testing.T) {
	for _, p := range []string{"/var/log/system.log", "/Library/Logs/DiagnosticReports/*.ips", "/Users/*/Library/Logs/*.log", "/var/log/wifi.log.0"} {
		if _, err := posixPaths.allowedFilePattern(p, MacOSLogFileRoots); err != nil {
			t.Errorf("%s refused: %v", p, err)
		}
	}
	for _, p := range []string{"/etc/passwd", "/var/log/../../etc/master.passwd.log", "/Users/bob/.ssh/id_rsa", "/var/LOG/system.log", "/var/log/system.log.gz", "var/log/x.log"} {
		if _, err := posixPaths.allowedFilePattern(p, MacOSLogFileRoots); err == nil {
			t.Errorf("%s was allowed", p)
		}
	}
}

func writeFile(t *testing.T, path string, data []byte, mod time.Time) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, data, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, mod, mod); err != nil {
		t.Fatal(err)
	}
}

func TestCollectFileLogs(t *testing.T) {
	dir := t.TempDir()
	root := filepath.Join(dir, "logs")
	now := time.Now()
	writeFile(t, filepath.Join(root, "app", "new.log"), []byte("line one\nERROR two\n"), now.Add(-time.Hour))
	writeFile(t, filepath.Join(root, "app", "old.log"), []byte("old\n"), now.Add(-100*time.Hour))
	writeFile(t, filepath.Join(root, "app", "binary.log"), []byte("MZ\x00\x01\x02\x00\x00"), now)
	writeFile(t, filepath.Join(root, "app", "notes.cfg"), []byte("secret=1"), now)
	writeFile(t, filepath.Join(dir, "outside.log"), []byte("outside"), now)
	if err := os.Symlink(filepath.Join(dir, "outside.log"), filepath.Join(root, "app", "link.log")); err != nil {
		t.Fatal(err)
	}
	roots := []string{root}
	specs := []LogSpec{
		{Source: "file:" + filepath.Join(root, "app", "*.log"), Window: 24 * time.Hour},
		{Source: "file:" + filepath.Join(root, "app", "old.log"), Window: 24 * time.Hour},
		{Source: "file:" + filepath.Join(dir, "outside.log"), Window: 24 * time.Hour},
		{Source: "file:" + filepath.Join(root, "missing.log"), Window: 24 * time.Hour},
	}
	text, sources := collectFileLogs(context.Background(), specs, roots, posixPaths, now)

	newSrc := "file:" + filepath.Join(root, "app", "new.log")
	oldSrc := "file:" + filepath.Join(root, "app", "old.log")
	if strings.Join(sources, ",") != newSrc+","+oldSrc {
		t.Fatalf("sources = %v", sources)
	}
	for _, want := range []string{
		"### " + newSrc + " Log\n[file modified",
		"ERROR two",
		"### " + oldSrc + " Log",
		"[unavailable: not a text file]",
		"### file:" + filepath.Join(dir, "outside.log") + " Log\n[refused: not inside an allowlisted log folder]",
		"### file:" + filepath.Join(root, "missing.log") + " Log\n[no matching log files found]",
	} {
		if !strings.Contains(text, want) {
			t.Errorf("missing %q in:\n%s", want, text)
		}
	}
	for _, unwanted := range []string{"outside\n", "secret=1"} {
		if strings.Contains(text, unwanted) {
			t.Errorf("read %q:\n%s", unwanted, text)
		}
	}
	// Each file is its own bundle entry.
	files := SplitLogSections(text)
	if len(files) < 4 {
		t.Fatalf("sections = %d", len(files))
	}
}

func TestReadLogTail(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "big.log")
	var b strings.Builder
	for i := 0; i < 1000; i++ {
		b.WriteString("0123456789 line\n")
	}
	writeFile(t, path, []byte(b.String()), time.Now())
	text, read, err := readLogTail(path, int64(b.Len()), 100)
	if err != nil || read != 100 {
		t.Fatalf("read=%d err=%v", read, err)
	}
	if !strings.HasPrefix(text, "0123456789 line\n") {
		t.Fatalf("partial first line kept: %q", text)
	}

	// UTF-16LE with BOM, as some Windows setup logs are written.
	u := utf16.Encode([]rune("Setup failed 0x80070005\r\n"))
	raw := []byte{0xFF, 0xFE}
	for _, c := range u {
		raw = append(raw, byte(c), byte(c>>8))
	}
	path16 := filepath.Join(dir, "setup.log")
	writeFile(t, path16, raw, time.Now())
	text, _, err = readLogTail(path16, int64(len(raw)), 1<<20)
	if err != nil || !strings.Contains(text, "Setup failed 0x80070005") {
		t.Fatalf("utf16 text=%q err=%v", text, err)
	}
}

func TestResolveLogRequestsAcceptsFiles(t *testing.T) {
	allowed := func(s string) bool {
		if strings.HasPrefix(s, FileSourcePrefix) {
			_, err := windowsPaths.allowedFilePattern(strings.TrimPrefix(s, FileSourcePrefix), WindowsLogFileRoots)
			return err == nil
		}
		return isWindowsLogSource(s)
	}
	specs, refused := ResolveLogRequests([]LogRequest{
		{Source: "System", Hours: 4},
		{Source: `file:C:\Windows\Logs\CBS\CBS.log`, Hours: 24},
		{Source: `file:C:\Windows\System32\config\SAM`},
	}, allowed)
	if len(specs) != 2 || len(refused) != 1 {
		t.Fatalf("specs=%v refused=%v", specs, refused)
	}
}

func TestFileRootsAcceptsValidExtraFolders(t *testing.T) {
	roots, ignored := FileRoots(WindowsLogFileRoots, windowsPaths, []string{
		`C:\ProgramData\Vendor\Logs`,
		`C:/Program Files/Vendor/logs`,
		`C:\Users\*\AppData\Local\Vendor`,
		"",
		`C:\`,
		`C:\Users`,
		`C:\Users\*`,
		`C:\*\Vendor\Logs`,
		`C:\ProgramData\Ven*\Logs`,
		`C:\ProgramData\..\Windows\System32`,
		`\\server\share\logs`,
		`/var/vendor/logs`,
	})
	if got := roots[len(WindowsLogFileRoots):]; strings.Join(got, "|") != `C:\ProgramData\Vendor\Logs|C:/Program Files/Vendor/logs|C:\Users\*\AppData\Local\Vendor` {
		t.Fatalf("extra roots = %v", got)
	}
	if len(ignored) != 8 {
		t.Fatalf("ignored = %v", ignored)
	}
	if _, err := windowsPaths.allowedFilePattern(`C:\Program Files\Vendor\logs\agent.log`, roots); err != nil {
		t.Fatalf("extra folder not usable: %v", err)
	}
	if _, err := windowsPaths.allowedFilePattern(`C:\Program Files\Vendor\agent.log`, roots); err == nil {
		t.Fatal("parent of an extra folder was allowed")
	}
	if roots, _ := FileRoots(nil, posixPaths, []string{"/opt/vendor/logs"}); roots != nil {
		t.Fatalf("platform without file logs gained roots: %v", roots)
	}
}

func TestAgentUsesExtraLogFoldersFromRequest(t *testing.T) {
	a := NewAgent()
	a.CallLLM = nil
	var gotRoots []string
	a.CollectLogs = func(_ context.Context, specs []LogSpec) (string, []string, error) {
		gotRoots = a.roots()
		return "", nil, nil
	}
	_, _ = a.Run(context.Background(), Request{Mode: ModeCollectLogs, ExtraLogFolders: []string{"/opt/vendor/logs"}})
	if len(platformFileRoots) == 0 {
		if gotRoots != nil {
			t.Fatalf("roots = %v", gotRoots)
		}
		return
	}
	if gotRoots[len(gotRoots)-1] != "/opt/vendor/logs" && platformPathStyle == posixPaths {
		t.Fatalf("roots = %v", gotRoots)
	}
}
