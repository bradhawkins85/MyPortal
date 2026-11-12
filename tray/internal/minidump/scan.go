package minidump

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	// MaxDumps is how many of the newest dumps one scan reports.
	MaxDumps = 20

	maxFileBytes = 64 << 20
	// A dump modified this recently may still be being written.
	settleTime = 2 * time.Minute
)

type cacheEntry struct {
	Size     int64  `json:"size"`
	Modified string `json:"modified"`
}

type candidate struct {
	name     string
	size     int64
	modified time.Time
}

func (c candidate) entry() cacheEntry {
	return cacheEntry{Size: c.size, Modified: c.modified.UTC().Format(time.RFC3339Nano)}
}

func loadCache(path string) map[string]cacheEntry {
	cache := map[string]cacheEntry{}
	raw, err := os.ReadFile(path)
	if err != nil {
		return cache
	}
	if json.Unmarshal(raw, &cache) != nil || cache == nil {
		return map[string]cacheEntry{}
	}
	return cache
}

func saveCache(path string, cache map[string]cacheEntry) error {
	raw, err := json.Marshal(cache)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".minidumps-*.tmp")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return err
	}
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

func listDumps(dir string, now time.Time) ([]candidate, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, nil
		}
		return nil, err
	}
	var found []candidate
	for _, entry := range entries {
		if !strings.EqualFold(filepath.Ext(entry.Name()), ".dmp") {
			continue
		}
		info, err := os.Lstat(filepath.Join(dir, entry.Name()))
		if err != nil || !info.Mode().IsRegular() || info.Size() > maxFileBytes {
			continue
		}
		if now.Sub(info.ModTime()) < settleTime {
			continue
		}
		found = append(found, candidate{name: entry.Name(), size: info.Size(), modified: info.ModTime()})
	}
	sort.Slice(found, func(i, j int) bool {
		if !found[i].modified.Equal(found[j].modified) {
			return found[i].modified.After(found[j].modified)
		}
		return found[i].name < found[j].name
	})
	if len(found) > MaxDumps {
		found = found[:MaxDumps]
	}
	return found, nil
}

func readDump(path string) ([]byte, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	return io.ReadAll(io.LimitReader(file, maxReadBytes))
}

// Scan decodes the newest dumps in dir that have not been reported yet.
// commit records them as reported; call it only after MyPortal has accepted
// them, so a failed upload is retried on the next scan.
func Scan(dir, cachePath string, now time.Time) ([]Summary, func() error, error) {
	noop := func() error { return nil }
	if dir == "" {
		return nil, noop, nil
	}
	found, err := listDumps(dir, now)
	if err != nil || len(found) == 0 {
		return nil, noop, err
	}
	cache := loadCache(cachePath)
	next := make(map[string]cacheEntry, len(found))
	var pending []Summary
	for _, c := range found {
		if cached, ok := cache[c.name]; ok && cached == c.entry() {
			next[c.name] = cached
			continue
		}
		data, err := readDump(filepath.Join(dir, c.name))
		if err != nil {
			continue // unreadable now; try again next scan
		}
		summary := Parse(data)
		summary.FileName = c.name
		summary.FileSize = c.size
		summary.FileModifiedAt = c.modified.UTC().Format(time.RFC3339)
		pending = append(pending, summary)
		next[c.name] = c.entry()
	}
	if len(pending) == 0 {
		return nil, noop, nil
	}
	return pending, func() error { return saveCache(cachePath, next) }, nil
}
