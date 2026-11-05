package live

import (
	"context"
	"errors"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Disk usage works like ncdu and gncdu: a concurrent scan totals every
// folder beneath a path, then the technician drills down from the largest
// items. Sizes are apparent sizes (file lengths), links and Windows junctions
// are counted but not followed, and by default the scan stays on the drive
// or filesystem it started on.

const (
	duKeepFiles      = 100
	duMaxNodes       = 1_000_000
	duMaxItems       = 500
	duProgressPeriod = 500 * time.Millisecond
)

type duNode struct {
	name     string
	dir      bool
	parent   *duNode
	children []*duNode
	// own is the size of this file, or of the files directly in this folder
	// (including those not kept as nodes).
	own        int64
	size       int64
	files      int64
	dirs       int64
	otherFiles int64
	otherSize  int64
	err        string
	skipped    string
}

type duScan struct {
	id      string
	root    *duNode
	path    string
	cancel  context.CancelFunc
	done    chan struct{}
	started time.Time

	files, dirs, bytes, errors, nodes atomic.Int64
	current                           atomic.Value
	cancelled                         atomic.Bool
}

type duScanner struct {
	scan    *duScan
	ctx     context.Context
	sameFS  bool
	rootDev uint64
	sem     chan struct{}
	wg      sync.WaitGroup
}

func (sc *duScanner) walk(node *duNode, path string) {
	if sc.ctx.Err() != nil {
		return
	}
	sc.scan.current.Store(path)
	entries, err := os.ReadDir(path)
	if err != nil {
		node.err = err.Error()
		sc.scan.errors.Add(1)
		if entries == nil {
			return
		}
	}
	var files []*duNode
	var own int64
	for _, entry := range entries {
		if sc.ctx.Err() != nil {
			return
		}
		info, err := entry.Info()
		if err != nil {
			sc.scan.errors.Add(1)
			continue
		}
		childPath := filepath.Join(path, entry.Name())
		if info.IsDir() && info.Mode()&fs.ModeSymlink == 0 {
			child := &duNode{name: entry.Name(), dir: true, parent: node}
			sc.scan.dirs.Add(1)
			if reason := sc.skipReason(info, childPath); reason != "" {
				child.skipped = reason
				node.children = append(node.children, child)
				continue
			}
			if sc.scan.nodes.Add(1) > duMaxNodes {
				// Past the node limit, folders are still totalled but
				// cannot be opened in the results.
				child.skipped = "Too many folders to keep; its size is included in the folder above."
				node.children = append(node.children, child)
				sc.walkCollapsed(child, childPath)
				continue
			}
			node.children = append(node.children, child)
			select {
			case sc.sem <- struct{}{}:
				sc.wg.Add(1)
				go func(child *duNode, childPath string) {
					defer sc.wg.Done()
					defer func() { <-sc.sem }()
					sc.walk(child, childPath)
				}(child, childPath)
			default:
				sc.walk(child, childPath)
			}
			continue
		}
		size := info.Size()
		own += size
		sc.scan.files.Add(1)
		sc.scan.bytes.Add(size)
		files = append(files, &duNode{name: entry.Name(), own: size, size: size, parent: node})
	}
	node.own = own
	node.files = int64(len(files))
	if len(files) > duKeepFiles {
		sort.Slice(files, func(i, j int) bool { return files[i].own > files[j].own })
		for _, file := range files[duKeepFiles:] {
			node.otherFiles++
			node.otherSize += file.own
		}
		files = files[:duKeepFiles]
	}
	node.children = append(node.children, files...)
}

// walkCollapsed totals a folder without keeping nodes for its contents.
func (sc *duScanner) walkCollapsed(node *duNode, path string) {
	_ = filepath.WalkDir(path, func(p string, entry fs.DirEntry, err error) error {
		if sc.ctx.Err() != nil {
			return filepath.SkipAll
		}
		if err != nil {
			sc.scan.errors.Add(1)
			return nil
		}
		if p == path {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if sc.skipReason(info, p) != "" {
				return filepath.SkipDir
			}
			node.dirs++
			sc.scan.dirs.Add(1)
			return nil
		}
		node.own += info.Size()
		node.files++
		node.otherFiles++
		node.otherSize += info.Size()
		sc.scan.files.Add(1)
		sc.scan.bytes.Add(info.Size())
		return nil
	})
}

func (sc *duScanner) skipReason(info fs.FileInfo, path string) string {
	if isReparsePoint(info) {
		return "Junction or mount point; not followed."
	}
	if sc.sameFS {
		if dev, ok := deviceID(info); ok && dev != sc.rootDev {
			return "On another drive or filesystem; not scanned."
		}
	}
	return ""
}

// finalize totals sizes and counts from the leaves up once the scan ends.
func finalize(node *duNode) {
	node.size = node.own
	if !node.dir {
		return
	}
	for _, child := range node.children {
		if !child.dir {
			continue
		}
		finalize(child)
		node.size += child.size
		node.files += child.files
		node.dirs += child.dirs + 1
	}
}

func (s *duScan) progress() map[string]interface{} {
	current, _ := s.current.Load().(string)
	return map[string]interface{}{
		"scan": s.id, "path": s.path, "files": s.files.Load(), "dirs": s.dirs.Load(),
		"bytes": s.bytes.Load(), "errors": s.errors.Load(), "current": current,
		"elapsed_ms": time.Since(s.started).Milliseconds(),
	}
}

func (s *duScan) find(path string) (*duNode, error) {
	path = filepath.Clean(path)
	rel, err := filepath.Rel(s.path, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return nil, errors.New("that path is outside the scanned folder")
	}
	node := s.root
	if rel == "." {
		return node, nil
	}
	for _, part := range strings.Split(rel, string(filepath.Separator)) {
		var next *duNode
		for _, child := range node.children {
			if child.name == part {
				next = child
				break
			}
		}
		if next == nil {
			return nil, errors.New("that item is not in the scan results")
		}
		node = next
	}
	return node, nil
}

type duItem struct {
	Name    string  `json:"name"`
	Path    string  `json:"path"`
	Type    string  `json:"type"`
	Size    int64   `json:"size"`
	Files   int64   `json:"files"`
	Dirs    int64   `json:"dirs"`
	Percent float64 `json:"percent"`
	Error   string  `json:"error,omitempty"`
	Skipped string  `json:"skipped,omitempty"`
}

func (s *duScan) children(path string, limit int) (map[string]interface{}, error) {
	node, err := s.find(path)
	if err != nil {
		return nil, err
	}
	if !node.dir {
		return nil, errors.New("that item is a file")
	}
	if limit <= 0 || limit > duMaxItems {
		limit = duMaxItems
	}
	full := filepath.Clean(path)
	items := make([]duItem, 0, len(node.children))
	for _, child := range node.children {
		item := duItem{Name: child.name, Path: filepath.Join(full, child.name), Type: "file", Size: child.size,
			Error: child.err, Skipped: child.skipped}
		if child.dir {
			item.Type = "dir"
			item.Files, item.Dirs = child.files, child.dirs
		}
		if node.size > 0 {
			item.Percent = float64(child.size) * 100 / float64(node.size)
		}
		items = append(items, item)
	}
	sort.Slice(items, func(i, j int) bool {
		if items[i].Size != items[j].Size {
			return items[i].Size > items[j].Size
		}
		return items[i].Name < items[j].Name
	})
	truncated := len(items) > limit
	if truncated {
		items = items[:limit]
	}
	parent := ""
	if full != s.path {
		parent = filepath.Dir(full)
	}
	return map[string]interface{}{
		"scan": s.id, "root": s.path, "path": full, "parent": parent, "size": node.size,
		"files": node.files, "dirs": node.dirs, "error": node.err, "items": items, "truncated": truncated,
		"other_files": map[string]int64{"count": node.otherFiles, "size": node.otherSize},
	}, nil
}

// remove takes a deleted item out of the results and its folders' totals.
func (s *duScan) remove(path string) error {
	node, err := s.find(path)
	if err != nil {
		return err
	}
	if node.parent == nil {
		return errors.New("the scanned folder itself cannot be removed from the results")
	}
	files, dirs := int64(1), int64(0)
	if node.dir {
		files, dirs = node.files, node.dirs+1
	}
	parent := node.parent
	kept := parent.children[:0]
	for _, child := range parent.children {
		if child != node {
			kept = append(kept, child)
		}
	}
	parent.children = kept
	if !node.dir {
		parent.own -= node.size
	}
	for ancestor := parent; ancestor != nil; ancestor = ancestor.parent {
		ancestor.size -= node.size
		ancestor.files -= files
		ancestor.dirs -= dirs
	}
	return nil
}

type duArgs struct {
	Scan           string `json:"scan"`
	Path           string `json:"path"`
	Limit          int    `json:"limit"`
	SameFilesystem *bool  `json:"same_filesystem"`
}

func (s *Session) diskScan(id string) (*duScan, error) {
	s.mu.Lock()
	scan := s.du
	s.mu.Unlock()
	if scan == nil || (id != "" && scan.id != id) {
		return nil, errors.New("run a new scan; these results are no longer available")
	}
	select {
	case <-scan.done:
	default:
		return nil, errors.New("the scan is still running")
	}
	return scan, nil
}

func (s *Session) stopDiskScan() {
	s.mu.Lock()
	scan := s.du
	s.du = nil
	s.mu.Unlock()
	if scan != nil {
		scan.cancel()
	}
}

func init() {
	register("diskusage.scan", func(s *Session, req Request) (interface{}, error) {
		var args duArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		path, err := cleanPath(args.Path)
		if err != nil {
			return nil, err
		}
		info, err := os.Stat(path)
		if err != nil {
			return nil, err
		}
		if !info.IsDir() {
			return nil, errors.New("choose a folder or drive to scan")
		}
		s.stopDiskScan()
		ctx, cancel := context.WithCancel(s.ctx)
		scan := &duScan{
			id: randomID(), path: path, cancel: cancel, done: make(chan struct{}), started: time.Now(),
			root: &duNode{name: path, dir: true},
		}
		scan.current.Store(path)
		sameFS := args.SameFilesystem == nil || *args.SameFilesystem
		rootDev, _ := deviceID(info)
		scanner := &duScanner{
			scan: scan, ctx: ctx, sameFS: sameFS, rootDev: rootDev,
			sem: make(chan struct{}, max(4, runtime.NumCPU()*2)),
		}
		s.mu.Lock()
		s.du = scan
		s.mu.Unlock()
		go func() {
			ticker := time.NewTicker(duProgressPeriod)
			defer ticker.Stop()
			finished := make(chan struct{})
			go func() {
				scanner.walk(scan.root, path)
				scanner.wg.Wait()
				close(finished)
			}()
			for {
				select {
				case <-finished:
					finalize(scan.root)
					scan.cancelled.Store(ctx.Err() != nil)
					close(scan.done)
					summary := scan.progress()
					summary["cancelled"] = scan.cancelled.Load()
					summary["size"] = scan.root.size
					s.event("diskusage.done", summary)
					return
				case <-ticker.C:
					s.event("diskusage.progress", scan.progress())
				}
			}
		}()
		return map[string]interface{}{"scan": scan.id, "path": path, "same_filesystem": sameFS}, nil
	})
	register("diskusage.cancel", func(s *Session, req Request) (interface{}, error) {
		var args duArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		s.mu.Lock()
		scan := s.du
		s.mu.Unlock()
		if scan != nil && (args.Scan == "" || scan.id == args.Scan) {
			scan.cancel()
		}
		return nil, nil
	})
	register("diskusage.children", func(s *Session, req Request) (interface{}, error) {
		var args duArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		scan, err := s.diskScan(args.Scan)
		if err != nil {
			return nil, err
		}
		path := args.Path
		if strings.TrimSpace(path) == "" {
			path = scan.path
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		return scan.children(path, args.Limit)
	})
	register("diskusage.remove", func(s *Session, req Request) (interface{}, error) {
		var args duArgs
		if err := decode(req, &args); err != nil {
			return nil, err
		}
		scan, err := s.diskScan(args.Scan)
		if err != nil {
			return nil, err
		}
		s.mu.Lock()
		defer s.mu.Unlock()
		if err := scan.remove(args.Path); err != nil {
			return nil, err
		}
		return map[string]interface{}{"size": scan.root.size}, nil
	})
}
