package minidump

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var scanNow = time.Date(2026, 11, 11, 3, 0, 0, 0, time.UTC)

func writeDump(t *testing.T, dir, name string, modified time.Time) string {
	t.Helper()
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, baseDump().bytes(), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(path, modified, modified); err != nil {
		t.Fatal(err)
	}
	return path
}

func mustScan(t *testing.T, dir, cache string) ([]Summary, func() error) {
	t.Helper()
	pending, commit, err := Scan(dir, cache, scanNow)
	if err != nil {
		t.Fatal(err)
	}
	return pending, commit
}

func TestScanReturnsNewDumpsOnce(t *testing.T) {
	dir, cache := t.TempDir(), filepath.Join(t.TempDir(), "minidumps.json")
	a := writeDump(t, dir, "a.dmp", scanNow.Add(-time.Hour))
	writeDump(t, dir, "B.DMP", scanNow.Add(-2*time.Hour))

	pending, commit := mustScan(t, dir, cache)
	if len(pending) != 2 {
		t.Fatalf("pending = %d", len(pending))
	}
	first := pending[0]
	if first.FileName != "a.dmp" || first.FileSize == 0 || first.FileModifiedAt != "2026-11-11T02:00:00Z" ||
		!first.Supported || first.BugcheckCode != 0xD1 {
		t.Fatalf("%+v", first)
	}
	if err := commit(); err != nil {
		t.Fatal(err)
	}
	if pending, _ := mustScan(t, dir, cache); len(pending) != 0 {
		t.Fatalf("after commit pending = %d", len(pending))
	}
	touched := scanNow.Add(-30 * time.Minute)
	if err := os.Chtimes(a, touched, touched); err != nil {
		t.Fatal(err)
	}
	if pending, _ := mustScan(t, dir, cache); len(pending) != 1 || pending[0].FileName != "a.dmp" {
		t.Fatalf("after touch pending = %+v", pending)
	}
}

func TestScanWithoutCommitRetries(t *testing.T) {
	dir, cache := t.TempDir(), filepath.Join(t.TempDir(), "minidumps.json")
	writeDump(t, dir, "a.dmp", scanNow.Add(-time.Hour))
	writeDump(t, dir, "b.dmp", scanNow.Add(-time.Hour))
	for i := 0; i < 2; i++ {
		if pending, _ := mustScan(t, dir, cache); len(pending) != 2 {
			t.Fatalf("scan %d pending = %d", i, len(pending))
		}
	}
}

func TestScanSkipsRecentlyWrittenFiles(t *testing.T) {
	dir, cache := t.TempDir(), filepath.Join(t.TempDir(), "minidumps.json")
	path := writeDump(t, dir, "a.dmp", scanNow.Add(-30*time.Second))
	if pending, _ := mustScan(t, dir, cache); len(pending) != 0 {
		t.Fatalf("a dump still being written was read: %+v", pending)
	}
	settled := scanNow.Add(-3 * time.Minute)
	if err := os.Chtimes(path, settled, settled); err != nil {
		t.Fatal(err)
	}
	if pending, _ := mustScan(t, dir, cache); len(pending) != 1 {
		t.Fatalf("pending = %d", len(pending))
	}
}

func TestScanLimits(t *testing.T) {
	dir, cache := t.TempDir(), filepath.Join(t.TempDir(), "minidumps.json")
	for i := 0; i < 25; i++ {
		writeDump(t, dir, fmt.Sprintf("d%02d.dmp", i), scanNow.Add(-time.Duration(i+10)*time.Minute))
	}
	big := writeDump(t, dir, "big.dmp", scanNow.Add(-5*time.Minute))
	if err := os.Truncate(big, 65<<20); err != nil {
		t.Fatal(err)
	}
	old := scanNow.Add(-5 * time.Minute)
	if err := os.Chtimes(big, old, old); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(dir, "x.dmp"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "notes.txt"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	pending, _ := mustScan(t, dir, cache)
	if len(pending) != 20 {
		t.Fatalf("pending = %d", len(pending))
	}
	for i, s := range pending {
		if want := fmt.Sprintf("d%02d.dmp", i); s.FileName != want {
			t.Fatalf("pending[%d] = %s, want %s (newest first)", i, s.FileName, want)
		}
	}
}

func TestScanMissingFolder(t *testing.T) {
	for _, dir := range []string{"", filepath.Join(t.TempDir(), "missing")} {
		pending, commit, err := Scan(dir, filepath.Join(t.TempDir(), "c.json"), scanNow)
		if err != nil || len(pending) != 0 || commit() != nil {
			t.Fatalf("dir %q: %v %v", dir, pending, err)
		}
	}
}

func TestScanCorruptCache(t *testing.T) {
	dir, cache := t.TempDir(), filepath.Join(t.TempDir(), "minidumps.json")
	writeDump(t, dir, "a.dmp", scanNow.Add(-time.Hour))
	if err := os.WriteFile(cache, []byte("not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if pending, _ := mustScan(t, dir, cache); len(pending) != 1 {
		t.Fatalf("pending = %d", len(pending))
	}
}

func TestScanCachePrunesDeletedFiles(t *testing.T) {
	dir, cache := t.TempDir(), filepath.Join(t.TempDir(), "minidumps.json")
	gone := writeDump(t, dir, "gone.dmp", scanNow.Add(-time.Hour))
	writeDump(t, dir, "kept.dmp", scanNow.Add(-time.Hour))
	_, commit := mustScan(t, dir, cache)
	if err := commit(); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(gone); err != nil {
		t.Fatal(err)
	}
	writeDump(t, dir, "new.dmp", scanNow.Add(-time.Hour))
	_, commit = mustScan(t, dir, cache)
	if err := commit(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(cache)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw), "gone.dmp") || !strings.Contains(string(raw), "kept.dmp") || !strings.Contains(string(raw), "new.dmp") {
		t.Fatalf("cache = %s", raw)
	}
	info, _ := os.Stat(cache)
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("cache mode = %v", info.Mode().Perm())
	}
}
