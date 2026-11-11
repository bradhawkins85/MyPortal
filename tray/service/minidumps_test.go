package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/bradhawkins85/myportal-tray/internal/minidump"
)

// A file that is not a dump is still reported (as one MyPortal cannot
// decode), which is all these tests need.
func writeFakeDumps(t *testing.T, dir string, count int) {
	t.Helper()
	old := time.Now().Add(-time.Hour)
	for i := 0; i < count; i++ {
		path := filepath.Join(dir, fmt.Sprintf("d%02d.dmp", i))
		if err := os.WriteFile(path, []byte("not a dump"), 0o600); err != nil {
			t.Fatal(err)
		}
		if err := os.Chtimes(path, old, old); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRunMinidumpScanRetriesAfterAFailedUpload(t *testing.T) {
	dir, cache := t.TempDir(), filepath.Join(t.TempDir(), "minidumps.json")
	writeFakeDumps(t, dir, 2)
	failing := func(context.Context, []minidump.Summary) error { return errors.New("offline") }
	if _, err := runMinidumpScan(context.Background(), dir, cache, failing); err == nil {
		t.Fatal("a failed upload must be reported")
	}
	var posted int
	ok := func(_ context.Context, dumps []minidump.Summary) error { posted += len(dumps); return nil }
	if n, err := runMinidumpScan(context.Background(), dir, cache, ok); err != nil || n != 2 || posted != 2 {
		t.Fatalf("n=%d posted=%d err=%v", n, posted, err)
	}
	posted = 0
	if n, err := runMinidumpScan(context.Background(), dir, cache, ok); err != nil || n != 0 || posted != 0 {
		t.Fatalf("second run: n=%d posted=%d err=%v", n, posted, err)
	}
}

func TestRunMinidumpScanPostsInBatches(t *testing.T) {
	// The scanner returns at most 20, so batching is exercised directly.
	var sizes []int
	post := func(_ context.Context, dumps []minidump.Summary) error { sizes = append(sizes, len(dumps)); return nil }
	if err := postMinidumpBatches(context.Background(), make([]minidump.Summary, 45), post); err != nil {
		t.Fatal(err)
	}
	if len(sizes) != 3 || sizes[0] != 20 || sizes[1] != 20 || sizes[2] != 5 {
		t.Fatalf("batches = %v", sizes)
	}
}

func TestRunMinidumpScanWithoutAFolder(t *testing.T) {
	called := false
	post := func(context.Context, []minidump.Summary) error { called = true; return nil }
	if n, err := runMinidumpScan(context.Background(), "", filepath.Join(t.TempDir(), "c.json"), post); err != nil || n != 0 || called {
		t.Fatalf("n=%d err=%v called=%v", n, err, called)
	}
}
